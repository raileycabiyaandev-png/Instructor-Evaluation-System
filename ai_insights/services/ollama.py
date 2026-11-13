# ai_insights/services/ollama.py
#
# Small client for a LOCAL Ollama server (https://ollama.com). It uses only Python's standard
# library, so no new package is needed. The text never leaves your own computer/server.
#
# Ollama WRITES text only. It never decides scores, forecasts or sentiment.
#   1. summarize_comments()  -> a summary of what students wrote + what they liked + what to improve
#   2. recommend()           -> a short recommendation for each category
#
# Safety, in this order:
#   * Comments are cleaned first (e-mails, web links, phone numbers removed), put in alphabetical
#     order (so the order of sending says nothing) and never saved by this file.
#   * The answer must be JSON in a fixed shape. Anything else is rejected.
#   * Every number in the answer must exist in the facts we gave.
#   * An answer that copies a long piece of one comment is rejected (keeps students anonymous).
#   * A rejected answer is never shown. The caller uses the rule-based text instead.

import json
import re
import urllib.error
import urllib.request

from .. import conf
from .data import CATEGORIES


class OllamaUnavailable(Exception):
    """Ollama is off, slow, or answered badly. The caller falls back to rule text."""


# ─────────────────────────────────────────────
# Low level: talk to Ollama
# ─────────────────────────────────────────────
def _post(path, payload, timeout=None):
    request = urllib.request.Request(
        conf.ollama_url().rstrip('/') + path,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or conf.ollama_timeout()) as response:
            return json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OllamaUnavailable(f"Ollama could not be reached ({type(exc).__name__}).") from exc


def generate(prompt, system=None, as_json=False, max_tokens=300):
    """Send one prompt, return the text. Raises OllamaUnavailable on any problem."""
    if not conf.ollama_enabled():
        raise OllamaUnavailable("Ollama is switched off in settings.")

    payload = {
        'model': conf.ollama_model(),
        'prompt': prompt,
        'stream': False,
        'options': {'temperature': 0.2, 'num_predict': max_tokens},
    }
    if system:
        payload['system'] = system
    if as_json:
        payload['format'] = 'json'

    body = _post('/api/generate', payload)
    text = str(body.get('response', '')).strip()
    if not text:
        raise OllamaUnavailable("Ollama returned an empty answer.")
    return text


def status():
    """
    Is Ollama ready? Used by the Generate panel to show a small status chip.
    Returns {'state': 'off'|'down'|'no_model'|'ready', 'model': name, 'text': short words}.
    It waits at most 2 seconds and never raises.
    """
    model = conf.ollama_model()
    if not conf.ollama_enabled():
        return {'state': 'off', 'model': model, 'text': 'Ollama is switched off'}
    try:
        request = urllib.request.Request(conf.ollama_url().rstrip('/') + '/api/tags', method='GET')
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {'state': 'down', 'model': model, 'text': 'Ollama is not running'}
    names = [str(m.get('name', '')) for m in body.get('models', []) if isinstance(m, dict)]
    wanted = model.split(':')[0]
    if not any(n == model or n.split(':')[0] == wanted for n in names):
        return {'state': 'no_model', 'model': model, 'text': f'Model "{model}" is not installed'}
    return {'state': 'ready', 'model': model, 'text': f'Ollama ready ({model})'}


# ─────────────────────────────────────────────
# Checks on the answer
# ─────────────────────────────────────────────
def numbers_are_supported(text, facts):
    """
    Safety check: every number in the AI text must also appear in the facts we gave.
    (Plain 1-5 numbers like 'out of 5' are allowed.) This blocks made-up figures.
    """
    allowed = set(re.findall(r'\d+(?:\.\d+)?', facts)) | {'1', '2', '3', '4', '5'}
    return all(found in allowed for found in re.findall(r'\d+(?:\.\d+)?', text))


_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_URL = re.compile(r'(?:https?://|www\.)\S+', re.IGNORECASE)
_PHONE = re.compile(r'\+?\d[\d\s().-]{6,}\d')
_HANDLE = re.compile(r'(?<!\w)@\w+')


def scrub(text):
    """Remove e-mails, links, phone numbers and @handles from a comment before it goes to Ollama."""
    for pattern in (_EMAIL, _URL, _PHONE, _HANDLE):
        text = pattern.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def copies_a_comment(answer, comments, window=7):
    """True when the answer repeats `window` words in a row from one single comment."""
    grams = set()
    for c in comments:
        w = _words(c)
        for i in range(len(w) - window + 1):
            grams.add(tuple(w[i:i + window]))
    a = _words(answer)
    return any(tuple(a[i:i + window]) in grams for i in range(len(a) - window + 1))


def _parse_json(text):
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise OllamaUnavailable("The AI answer was not JSON.")
        try:
            data = json.loads(match.group(0))
        except ValueError as exc:
            raise OllamaUnavailable("The AI answer was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise OllamaUnavailable("The AI answer had the wrong shape.")
    return data


def _clean_text(value, limit):
    if not isinstance(value, str):
        raise OllamaUnavailable("The AI answer had a field of the wrong type.")
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def _clean_points(value, limit_each=180, limit_count=5):
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise OllamaUnavailable("The AI answer had a list of the wrong type.")
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(_clean_text(item, limit_each))
    return out[:limit_count]


# ─────────────────────────────────────────────
# 1. Summary of comments
# ─────────────────────────────────────────────
SUMMARY_SYSTEM = (
    "You summarise anonymous student comments about a teacher, for the teacher. "
    "Use ONLY what the comments and the facts say. Never invent anything. Never guess who wrote a comment. "
    "Never quote a comment word for word. Write in simple English, even if comments are in Filipino. "
    "The summary must have 3 to 5 sentences: the general mood, what students liked, what they want improved, "
    "and one closing sentence. "
    "Answer with JSON only, in this exact shape: "
    '{"summary": "3 to 5 sentences", "positive_points": ["..."], "negative_points": ["..."]}. '
    "positive_points are things students liked. negative_points are things students want improved. "
    "Each point is one short sentence. Use an empty list when there is nothing for it."
)

MAX_COMMENTS = 60
MAX_COMMENT_CHARS = 300


def summarize_comments(comments, facts):
    """
    comments: list of cleaned comment strings (no names). facts: the numbers we computed ourselves.
    Returns {'summary', 'positive_points', 'negative_points'} or raises OllamaUnavailable.
    """
    cleaned = sorted({scrub(c)[:MAX_COMMENT_CHARS] for c in comments if scrub(c)})[:MAX_COMMENTS]
    if not cleaned:
        raise OllamaUnavailable("No comment was left after cleaning.")

    listing = "\n".join(f"- {c}" for c in cleaned)
    prompt = (
        f"Facts (computed by the system, they are correct):\n{facts}\n\n"
        f"Anonymous student comments:\n{listing}\n\n"
        "Write the JSON now."
    )
    data = _parse_json(generate(prompt, system=SUMMARY_SYSTEM, as_json=True, max_tokens=500))

    summary = _clean_text(data.get('summary', ''), 900)
    positive = _clean_points(data.get('positive_points'))
    negative = _clean_points(data.get('negative_points'))
    if len(summary) < 20:
        raise OllamaUnavailable("The AI summary was empty or too short.")
    if len(re.findall(r'[.!?](?:\s|$)', summary)) < 3:
        raise OllamaUnavailable("The AI summary had fewer than 3 sentences, so the rule-based summary is used.")

    # Matching the real counts: no "liked" list when no comment was positive, and the same for "improve".
    counts = _counts_in(facts)
    if counts.get('positive') == 0:
        positive = []
    if counts.get('negative') == 0:
        negative = []

    for text in [summary, *positive, *negative]:
        if not numbers_are_supported(text, facts):
            raise OllamaUnavailable("The AI text had numbers that are not in the facts, so it was rejected.")
        if copies_a_comment(text, cleaned):
            raise OllamaUnavailable("The AI text copied a comment, so it was rejected.")
        if _EMAIL.search(text) or _URL.search(text):
            raise OllamaUnavailable("The AI text had a link or e-mail, so it was rejected.")
    return {'summary': summary, 'positive_points': positive, 'negative_points': negative}


def _counts_in(facts):
    out = {}
    for key in ('positive', 'negative'):
        m = re.search(rf'{key} comments?: (\d+)', facts, re.IGNORECASE)
        if m:
            out[key] = int(m.group(1))
    return out


# ─────────────────────────────────────────────
# 2. Recommendations for the categories
# ─────────────────────────────────────────────
RECOMMEND_SYSTEM = (
    "You are a helpful teaching coach. Write short, kind, practical advice for a teacher. "
    "Use ONLY the facts you are given for each category. Never invent numbers or events. "
    "Each category text is 2 to 3 sentences. Speak about that category only. "
    "Every category text must be different: name that category's own score, change or topics, and never reuse a sentence. "
    "If the facts list student topics, base the advice on those topics. "
    "If a category is a strength, congratulate briefly and do not suggest a fix. "
    "Answer with JSON only, in this exact shape: "
    '{"overall": "2 sentences", "categories": {"<key>": "advice", ...}}. '
    "Use the exact keys you are given."
)


def recommend(facts, category_keys):
    """
    facts: text built by pipeline.coaching_facts(). category_keys: the keys we want advice for.
    Returns {'overall': text or '', 'categories': {key: text}} with ONLY the texts that passed all checks.
    Raises OllamaUnavailable when nothing usable came back.
    """
    prompt = (
        f"Facts (the only information you may use):\n{facts}\n\n"
        f"Write advice for these keys: {', '.join(category_keys)}.\nWrite the JSON now."
    )
    data = _parse_json(generate(prompt, system=RECOMMEND_SYSTEM, as_json=True, max_tokens=700))

    labels = dict(CATEGORIES)
    result, seen = {}, set()
    raw = data.get('categories')
    if not isinstance(raw, dict):
        raise OllamaUnavailable("The AI answer had no categories.")
    for key in category_keys:
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        text = _clean_text(value, 420)
        if len(text) < 20 or not numbers_are_supported(text, facts):
            continue
        # a text about one category must not talk about another category
        others = [lab for k, lab in labels.items() if k != key]
        if any(lab.lower() in text.lower() for lab in others):
            continue
        norm = re.sub(r'\W+', ' ', text.lower()).strip()
        if norm in seen:                       # the same sentence for two categories is not real advice
            continue
        seen.add(norm)
        result[key] = text

    overall = ''
    if isinstance(data.get('overall'), str):
        candidate = _clean_text(data['overall'], 420)
        if len(candidate) >= 20 and numbers_are_supported(candidate, facts):
            overall = candidate
    if not result and not overall:
        raise OllamaUnavailable("None of the AI texts passed the safety checks.")
    return {'overall': overall, 'categories': result}


# ─────────────────────────────────────────────
# 3. School-level overview (counts only, never comments)
# ─────────────────────────────────────────────
SCHOOL_SYSTEM = (
    "You write a short school-wide report about teaching evaluations for the school administrator. "
    "Use ONLY the facts you are given. Never invent numbers, names or events. Never mention a single instructor. "
    "Write in simple English. Answer with JSON only, in this exact shape: "
    '{"summary": "3 to 4 short sentences", "strengths": ["..."], "weaknesses": ["..."], "suggestions": ["..."]}. '
    "strengths: what is going well. weaknesses: what is weaker. suggestions: practical school-wide actions "
    "that match the weaknesses. Each item is one short sentence. Use an empty list when the facts have nothing for it."
)


def school_overview(facts):
    """
    facts: text built by services/school.py (numbers and topic counts only).
    Returns {'summary', 'strengths', 'weaknesses', 'suggestions'} or raises OllamaUnavailable.
    """
    prompt = f"Facts (the only information you may use):\n{facts}\n\nWrite the JSON now."
    data = _parse_json(generate(prompt, system=SCHOOL_SYSTEM, as_json=True, max_tokens=700))
    summary = _clean_text(data.get('summary', ''), 800)
    result = {
        'summary': summary,
        'strengths': _clean_points(data.get('strengths')),
        'weaknesses': _clean_points(data.get('weaknesses')),
        'suggestions': _clean_points(data.get('suggestions')),
    }
    if len(summary) < 20:
        raise OllamaUnavailable("The AI summary was empty or too short.")
    if len(re.findall(r'[.!?](?:\s|$)', summary)) < 3:
        raise OllamaUnavailable("The AI summary had fewer than 3 sentences, so the rule-based summary is used.")
    for text in [summary, *result['strengths'], *result['weaknesses'], *result['suggestions']]:
        if not numbers_are_supported(text, facts):
            raise OllamaUnavailable("The AI text had numbers that are not in the facts, so it was rejected.")
        if _EMAIL.search(text) or _URL.search(text):
            raise OllamaUnavailable("The AI text had a link or e-mail, so it was rejected.")
    return result


# ─────────────────────────────────────────────
# Older single-note writer (kept for compatibility with older code)
# ─────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You write short, kind, plain-English notes for a teacher. "
    "Use ONLY the facts you are given. Never invent numbers, names or events. "
    "Do not guess who wrote anything. Write at most 90 words. No lists, no headings."
)


def write_text(facts):
    """Ask Ollama to turn FACTS into a short note. Returns clean text or raises."""
    prompt = (
        "Facts (the only information you may use):\n"
        f"{facts}\n\n"
        "Write the short note for the teacher now."
    )
    text = generate(prompt, system=SYSTEM_PROMPT)
    text = re.sub(r'\s+', ' ', text).strip()[:900]
    if not numbers_are_supported(text, facts):
        raise OllamaUnavailable("The AI text had numbers that are not in the facts, so it was rejected.")
    return text