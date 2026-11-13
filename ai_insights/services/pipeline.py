# ai_insights/services/pipeline.py
#
# The ONE place where AI work is done and SAVED.
# It is called by the management command and by the admin "Refresh" button.
# It is NEVER called while a student submits an evaluation or while a page loads.

import logging

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from evaluation.models import Evaluation

from .. import conf
from ..models import CommentSentiment, FeedbackSummary, ForecastResult, Suggestion
from . import alerts, forecasting, notices, ollama, sentiment, themes
from .data import (CATEGORIES, analyse_history, finished_periods, hash_of,
                   instructor_series, period_label)
from .suggestions import build_rule_suggestions

log = logging.getLogger(__name__)

# Change this when the comment/topic method changes, so old saved summaries are made again.
FEEDBACK_ENGINE = 'feedback-v3'
SUGGESTION_ENGINE = 'suggest-v3'


# ─────────────────────────────────────────────
# 1. FORECAST
# ─────────────────────────────────────────────
def run_forecast(instructor, series, quality=None, target_period=None, force=False):
    """
    Save the forecast of ONE instructor for ONE period (always one row per instructor per period).
    `target_period`: the period the administrator chose. The forecast must be based on exactly that
    period; if the instructor has no usable data in it (for example fewer than 3 responses), a clear
    'not enough data' row is saved instead of silently using an older period.
    Returns 'created', 'skipped' or 'error'.
    """
    quality = quality or {}
    key_period = target_period or (series[-1]["period"] if series else None)
    mismatch = bool(target_period) and (not series or series[-1]["period"].pk != target_period.pk)
    fingerprint = hash_of(
        forecasting.forecast_fingerprint(series), quality.get('skipped', []),
        key_period.pk if key_period else None, mismatch,
    )

    existing = ForecastResult.objects.filter(instructor=instructor, based_on_period=key_period).first()
    if existing and not force and existing.input_hash == fingerprint and existing.status != 'error':
        return 'skipped'

    try:
        if mismatch:
            result = forecasting.blocked_result(
                series, quality,
                f"The selected period ({period_label(target_period)}) has fewer than {conf.MIN_RESPONSES} "
                f"responses for this instructor, so it cannot be used. No forecast was made from it.")
        else:
            result = forecasting.make_forecast(series, quality)
    except Exception as exc:                       # never break the whole run
        log.exception("Forecast failed for instructor %s", instructor.pk)
        result = dict(status='error', message=f"The forecast could not be made ({type(exc).__name__}).",
                      history=[], periods_used=len(series), library_version='', data_quality=quality)

    fields = dict(
        status=result['status'],
        message=result.get('message', ''),
        target_label=result.get('target_label', ''),
        target=result.get('target', {}),
        data_quality=result.get('data_quality', {}),
        predicted_overall=result.get('predicted_overall'),
        range_low=result.get('range_low'),
        range_high=result.get('range_high'),
        predicted_categories=result.get('predicted_categories', {}),
        history=result.get('history', []),
        periods_used=result.get('periods_used', 0),
        model_name=result.get('model_name', ''),
        model_version=result.get('model_version', forecasting.MODEL_VERSION),
        backtest=result.get('backtest', []),
        validation_mae=result.get('validation_mae'),
        baseline_mae=result.get('baseline_mae'),
        validation_steps=result.get('validation_steps', 0),
        reliability=result.get('reliability', ''),
        library_version=result.get('library_version', ''),
        input_hash=fingerprint,
        generated_at=timezone.now(),
    )
    with transaction.atomic():
        if key_period is None:                      # no period at all: keep a single "nothing yet" row
            ForecastResult.objects.filter(instructor=instructor, based_on_period=None).delete()
            ForecastResult.objects.create(instructor=instructor, based_on_period=None, **fields)
        else:
            ForecastResult.objects.update_or_create(
                instructor=instructor, based_on_period=key_period, defaults=fields)
    return 'error' if result['status'] == 'error' else 'created'


# ─────────────────────────────────────────────
# 2. SENTIMENT + FEEDBACK SUMMARY (per finished period)
# ─────────────────────────────────────────────
def _comments_of(instructor, period):
    """[(evaluation_id, cleaned_text)] for usable comments, and the count of skipped ones."""
    rows = (
        Evaluation.objects.filter(teacher=instructor, period=period)
        .exclude(comment='').values_list('id', 'comment')
    )
    usable, skipped = [], 0
    for eval_id, text in rows:
        cleaned = sentiment.clean_comment(text)
        if cleaned is None:
            skipped += 1
        else:
            usable.append((eval_id, cleaned))
    return usable, skipped


def run_feedback(instructor, period, n_responses, use_llm=False, force=False):
    """Analyse comments of ONE finished period and save the summary. Returns a short status word."""
    model_name = conf.sentiment_model_name()
    base = dict(instructor=instructor, period=period)
    values = dict(n_responses=n_responses, generated_at=timezone.now(), sentiment_model=model_name)

    # Privacy rule 1: too few students -> do not analyse or show anything
    if n_responses < conf.MIN_RESPONSES:
        FeedbackSummary.objects.update_or_create(**base, defaults=dict(
            **values, hidden=True, n_comments=0, n_analyzed=0, n_skipped=0,
            positive=0, neutral=0, negative=0, themes=[], subthemes=[], positive_points=[], negative_points=[],
            summary_text='', summary_source='rule', llm_model='',
            hidden_reason=f"Fewer than {conf.MIN_RESPONSES} students evaluated, so comment analysis stays hidden.",
            input_hash='',
        ))
        return 'too_few_students'

    usable, skipped = _comments_of(instructor, period)
    n_comments = len(usable) + skipped
    fingerprint = hash_of([e for e, _ in usable], skipped, model_name, n_responses,
                          use_llm, conf.ollama_model() if use_llm else '', FEEDBACK_ENGINE)

    existing = FeedbackSummary.objects.filter(**base).first()
    llm_done = (not use_llm) or (existing is not None and existing.summary_source == 'ollama')
    if existing and existing.input_hash == fingerprint and llm_done and not force:
        return 'skipped'          # same data and nothing new to do

    # Privacy rule 2: too few usable comments -> show nothing
    if len(usable) < conf.MIN_COMMENTS:
        FeedbackSummary.objects.update_or_create(**base, defaults=dict(
            **values, hidden=True, n_comments=n_comments, n_analyzed=0, n_skipped=skipped,
            positive=0, neutral=0, negative=0, themes=[], subthemes=[], positive_points=[], negative_points=[],
            summary_text='', summary_source='rule', llm_model='',
            hidden_reason=f"Fewer than {conf.MIN_COMMENTS} usable comments, so no summary is shown.",
            input_hash=fingerprint,
        ))
        return 'too_few_comments'

    # Run the model only for comments that have no saved result yet (no repeated inference)
    saved = {s.evaluation_id: s for s in CommentSentiment.objects.filter(
        evaluation_id__in=[e for e, _ in usable], model_name=model_name)}
    todo = [(e, t) for e, t in usable if e not in saved]

    try:
        if todo:
            predictions = sentiment.classify([t for _, t in todo], model_name)
            with transaction.atomic():
                for (eval_id, _), (label, confidence) in zip(todo, predictions):
                    obj, _ = CommentSentiment.objects.update_or_create(
                        evaluation_id=eval_id,
                        defaults=dict(label=label, confidence=confidence, model_name=model_name,
                                      analyzed_at=timezone.now()),
                    )
                    saved[eval_id] = obj
    except sentiment.SentimentUnavailable as exc:
        FeedbackSummary.objects.update_or_create(**base, defaults=dict(
            **values, hidden=True, n_comments=n_comments, n_analyzed=0, n_skipped=skipped,
            positive=0, neutral=0, negative=0, themes=[], subthemes=[], positive_points=[], negative_points=[],
            summary_text='', summary_source='rule', llm_model='',
            hidden_reason=str(exc), input_hash='',          # '' = try again next run
        ))
        return 'model_unavailable'

    labels = [saved[e].label for e, _ in usable]
    pairs = [(t, saved[e].label) for e, t in usable]
    found = themes.find_themes(pairs)
    subs = themes.find_subtopics(pairs)
    counts = {k: labels.count(k) for k in ('positive', 'neutral', 'negative')}

    # Rule-based version first (always available), then the AI version if Ollama is used and passes the checks.
    text, source, llm_model = rule_summary_text(len(usable), counts, found, subs,
                                                 seed=f"{instructor.pk}-{period.pk}"), 'rule', ''
    positive_points, negative_points = rule_points(subs)
    if use_llm:
        facts = summary_facts(period_label(period), n_responses, len(usable), counts, found, subs)
        try:
            written = ollama.summarize_comments([t for _, t in usable], facts)
            text, source, llm_model = written['summary'], 'ollama', conf.ollama_model()
            positive_points, negative_points = written['positive_points'], written['negative_points']
        except ollama.OllamaUnavailable as exc:
            log.info("Ollama summary skipped: %s", exc)

    FeedbackSummary.objects.update_or_create(**base, defaults=dict(
        **values, hidden=False, hidden_reason='', n_comments=n_comments, n_analyzed=len(usable),
        n_skipped=skipped, themes=found, subthemes=subs, summary_text=text, summary_source=source,
        positive_points=positive_points, negative_points=negative_points,
        llm_model=llm_model, input_hash=fingerprint, **counts,
    ))
    return 'created'


def rule_points(subs):
    """What students liked / want improved, from the REAL topics found (used when Ollama is not used)."""
    liked = [f"{s['label']} ({s['mentions']} comments)" for s in subs if s['tone'] == 'praise'][:5]
    improve = [f"{s['label']} ({s['mentions']} comments)" for s in subs if s['tone'] == 'problem'][:5]
    return liked, improve


def _pick(seed, key, options):
    """Choose one wording from `options`. The same seed gives the same wording every time, but
    different instructors / periods / places get different wording, so texts do not all look alike."""
    return options[int(hash_of(str(seed), key)[:8], 16) % len(options)]


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def rule_summary_text(n_analyzed, counts, found, subs=None, seed=''):
    """
    The comment summary when Ollama is not used (or its answer was rejected): 4 to 5 sentences made
    ONLY from the real counts and the real topics. The wording changes with `seed`.
    """
    pos, neu, neg = counts['positive'], counts['neutral'], counts['negative']
    total = max(1, pos + neu + neg)
    problems = [s for s in (subs or []) if s['tone'] == 'problem']
    praise = [s for s in (subs or []) if s['tone'] == 'praise']

    if pos / total >= 0.6:
        tone = _pick(seed, 'tone-pos', [
            "Overall, the students' comments are mostly positive.",
            "Most students wrote kind and encouraging comments about your class.",
            "The general mood in the comments is favourable."])
    elif neg / total >= 0.5 and neg > pos:
        tone = _pick(seed, 'tone-neg', [
            "Overall, many comments point to things that need work.",
            "Most students wrote about problems they would like you to fix.",
            "The general mood in the comments is critical."])
    else:
        tone = _pick(seed, 'tone-mix', [
            "Overall, the comments are mixed: some praise and some requests for change.",
            "Students shared both good experiences and concerns about the class.",
            "The comments are balanced between what works and what could be better."])

    parts = [tone, f"Of {n_analyzed} usable comments, {pos} were positive, {neu} neutral and {neg} negative."]

    if praise:
        names = _join(f"{s['label'].lower()} ({s['mentions']})" for s in praise[:3])
        parts.append(_pick(seed, 'liked', [f"Students liked: {names}.", f"What students appreciated most: {names}.",
                                           f"Strong points that students named: {names}."]))
    if problems:
        names = _join(f"{s['label'].lower()} ({s['mentions']})" for s in problems[:3])
        parts.append(_pick(seed, 'improve', [f"Students want improved: {names}.",
                                             f"Topics students asked you to work on: {names}.",
                                             f"Concerns that came up more than once: {names}."]))
    if not praise and not problems:
        if found:
            top = _join(f"{t['label'].lower()} ({t['mentions']} comments)" for t in found[:3])
            parts.append(f"Students wrote most about: {top}.")
        else:
            parts.append("No single topic was mentioned often enough to be listed, which also protects privacy.")

    if problems and praise:
        parts.append(_pick(seed, 'close-both', [
            "Keep what students like and start with the first topic they asked you to improve.",
            "Build on your strong points while you work on one requested change at a time."]))
    elif problems:
        parts.append(_pick(seed, 'close-prob', [
            "Choosing one of these topics to improve first is a good next step.",
            "Small, visible changes in these topics will show up in your next evaluation."]))
    elif praise:
        parts.append(_pick(seed, 'close-good', [
            "Keep doing what works and share these practices with colleagues.",
            "These strengths are worth keeping as you plan your next lessons."]))
    else:
        parts.append("Read the comments in the Evaluation Results page for the full picture.")
    return " ".join(parts)


def summary_facts(label, n_responses, n_analyzed, counts, found, subs=None):
    lines = [
        f"Period: {label}.",
        f"Students who evaluated: {n_responses}.",
        f"Usable comments: {n_analyzed}.",
        f"Positive comments: {counts['positive']}.",
        f"Neutral comments: {counts['neutral']}.",
        f"Negative comments: {counts['negative']}.",
    ]
    for t in found[:5]:
        lines.append(f"Category '{t['label']}': {t['mentions']} comments "
                     f"({t['positive']} positive, {t['negative']} negative).")
    for s in (subs or [])[:8]:
        kind = 'students want this improved' if s['tone'] == 'problem' else 'students like this'
        lines.append(f"Topic '{s['label']}' ({kind}): {s['mentions']} comments.")
    lines.append("Task: summarise what students said, what they liked and what they want improved. No names.")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 3. SUGGESTIONS (for the latest finished period)
# ─────────────────────────────────────────────
def run_suggestions(instructor, series, forecast_row, summary, use_llm=False, force=False):
    latest = series[-1]
    previous = series[-2] if len(series) >= 2 else None

    forecast_dict = None
    if forecast_row is not None and forecast_row.status == 'ok':
        forecast_dict = {'status': 'ok', 'predicted_categories': forecast_row.predicted_categories}
    shown = summary is not None and not summary.hidden
    found = summary.themes if shown else []
    subs = summary.subthemes if shown else []

    rules = build_rule_suggestions(latest, previous, forecast_dict, found, subs,
                                   seed=f"{instructor.pk}-{latest['period'].pk}")
    period = latest["period"]

    with transaction.atomic():
        Suggestion.objects.filter(instructor=instructor, period=period, source='rule').delete()
        Suggestion.objects.bulk_create([
            Suggestion(instructor=instructor, period=period, category=r['category'],
                       category_label=r['category_label'], priority=r['priority'], text=r['text'],
                       source='rule', evidence=r['evidence'])
            for r in rules
        ])

    facts = coaching_facts(latest, previous, rules, forecast_row)
    facts_hash = hash_of(facts, conf.ollama_model(), SUGGESTION_ENGINE)
    already = Suggestion.objects.filter(instructor=instructor, period=period, source='ollama')

    if not use_llm:
        # AI texts written for DIFFERENT facts would not match the new rule text: remove them.
        for old in already:
            if old.evidence.get('facts_hash') != facts_hash:
                old.delete()
        return 'rules_only'

    if already.exists() and all(a.evidence.get('facts_hash') == facts_hash for a in already) and not force:
        return 'rules_and_llm'                    # same facts: do not ask Ollama again

    keys = [r['category'] for r in rules]
    try:
        written = ollama.recommend(facts, keys)
    except ollama.OllamaUnavailable as exc:
        log.info("Ollama recommendations skipped: %s", exc)
        already.delete()                  # never keep an old AI text next to new rule text
        return 'rules_only'

    by_key = {r['category']: r for r in rules}
    with transaction.atomic():
        Suggestion.objects.filter(instructor=instructor, period=period, source='ollama').delete()
        for key, text in written['categories'].items():
            r = by_key[key]
            Suggestion.objects.create(
                instructor=instructor, period=period, category=key, category_label=r['category_label'],
                priority=r['priority'], text=text, source='ollama', llm_model=conf.ollama_model(),
                evidence={**r['evidence'], 'facts_hash': facts_hash},
            )
        if written['overall']:
            Suggestion.objects.create(
                instructor=instructor, period=period, category='overall', category_label='Overall',
                priority='low', text=written['overall'], source='ollama', llm_model=conf.ollama_model(),
                evidence={'overall': round(latest['overall'], 2), 'responses': latest['responses'],
                          'period': latest['label'], 'facts_hash': facts_hash},
            )
    return 'rules_and_llm'


def coaching_facts(latest, previous, rules, forecast_row):
    """Only real numbers and real topic counts. Ollama may use nothing else."""
    lines = [
        f"Period: {latest['label']}. Responses: {latest['responses']}.",
        f"Overall score: {latest['overall']:.2f} out of 5.",
    ]
    if previous:
        lines.append(f"Overall score in the period before: {previous['overall']:.2f}.")
    for r in rules:
        e = r['evidence']
        line = f"[{r['category']}] {r['category_label']}: score {e['score']}, priority {r['priority']}."
        if e.get('change') is not None:
            line += f" Change since the last period: {e['change']}."
        for a in e.get('actions', []):
            if a['mentions']:
                line += f" Students want improved: {a['topic']} ({a['mentions']} comments)."
        for p in e.get('praise', []):
            line += f" Students like: {p['topic']} ({p['mentions']} comments)."
        lines.append(line)
    if forecast_row is not None and forecast_row.status == 'ok':
        lines.append(f"Forecast for next period: about {forecast_row.predicted_overall:.2f} "
                     f"(typical error {forecast_row.validation_mae}). This is a prediction, not a fact.")
    lines.append("Task: for each key, write short advice that matches ONLY that category's facts.")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 4. EVERYTHING FOR ONE INSTRUCTOR
# ─────────────────────────────────────────────
def _forecast_for(instructor, series):
    """The saved forecast of the newest usable period (or None)."""
    if not series:
        return None
    return ForecastResult.objects.filter(instructor=instructor, based_on_period=series[-1]['period']).first()


def run_for_instructor(instructor, use_sentiment=True, use_llm=False, force=False, periods=None,
                       target_period=None, run=None):
    """
    Returns a small report dict. Never raises for normal data problems.
    `periods`: the finished periods the analysis may use (the administrator's chosen period is the last).
    `target_period`: that chosen period. Without it the newest usable period is used.
    """
    report = {'instructor': str(instructor), 'forecast': None, 'feedback': {}, 'suggestions': None}
    periods = finished_periods() if periods is None else periods
    series, quality = analyse_history(instructor, periods)

    report['forecast'] = run_forecast(instructor, series, quality=quality,
                                      target_period=target_period, force=force)
    on_target = bool(series) and (target_period is None or series[-1]['period'].pk == target_period.pk)

    summary = None
    if use_sentiment:
        every = dict(
            Evaluation.objects.filter(teacher=instructor, period__in=periods)
            .values_list('period_id').annotate(c=Count('id'))
        )
        for period in periods:
            n = every.get(period.pk, 0)
            if n == 0:
                continue
            is_latest = bool(series) and period.pk == series[-1]['period'].pk
            report['feedback'][period.pk] = run_feedback(
                instructor, period, n, use_llm=use_llm and is_latest, force=force)
        if series:
            summary = FeedbackSummary.objects.filter(instructor=instructor, period=series[-1]['period']).first()

    if on_target:
        forecast_row = ForecastResult.objects.filter(
            instructor=instructor, based_on_period=series[-1]['period']).first()
        report['suggestions'] = run_suggestions(instructor, series, forecast_row, summary,
                                                use_llm=use_llm, force=force)
    else:
        report['suggestions'] = 'no_data'
    report['alerts'] = alerts.sync(instructor, series, _forecast_for(instructor, series), summary, run=run)
    report['notices'] = notices.sync(instructor, series)
    report['llm'] = {'summary': bool(summary is not None and summary.summary_source == 'ollama'),
                     'suggestions': report['suggestions'] == 'rules_and_llm'}
    return report


def run_for_all(**kwargs):
    from users.models import User
    periods = finished_periods()
    reports = []
    for teacher in User.objects.filter(role='instructor', is_active=True).order_by('id'):
        try:
            reports.append(run_for_instructor(teacher, periods=periods, **kwargs))
        except Exception:                              # one bad teacher must not stop the others
            log.exception("AI run failed for instructor %s", teacher.pk)
            reports.append({'instructor': str(teacher), 'error': True})
    return reports