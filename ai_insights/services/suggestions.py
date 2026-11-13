# ai_insights/services/suggestions.py
#
# RULE-BASED improvement suggestions. Every suggestion is made by a visible rule from REAL numbers
# and from the REAL topics students wrote about. The numbers and topics are saved with it (evidence),
# so a suggestion can never say something the data does not say.
#
# Priority of each of the five categories (score is out of 5):
#   score below 3.5                        -> "Needs attention"  (high)
#   score 3.5 - 4.49 AND (it is the lowest category
#        OR it fell 0.2+ since the last period
#        OR students' comments about it are mostly negative
#        OR the forecast says it will fall 0.2+
#        OR 2+ comments describe a problem in it)   -> "Can improve" (medium)
#   score 3.5 - 4.49 otherwise             -> "Small idea" (low)
#   score 4.5 or more                      -> "Strength"
#
# What the text says:
#   * The first sentence uses the real score and its change.
#   * Each STEP comes from a topic that students really wrote about (SUBTOPICS in themes.py).
#   * If no topic was clear enough, the text says so and does not invent a problem.
#   * If students praise a category that has a low score, the text says that the comments and the score disagree.

import hashlib

from .. import conf
from .data import CATEGORIES
from .themes import TOPIC_BY_KEY

# Used only when a category needs attention but the comments gave no clear topic.
FALLBACK_ACTION = {
    'teaching_effectiveness': "Ask your students in a short anonymous poll which part of your lessons is hardest to follow.",
    'communication_skills': "Ask your students in a short anonymous poll when it is hard to ask you a question.",
    'classroom_management': "Ask your students in a short anonymous poll what slows the class down.",
    'punctuality': "Check the real start and end times of your classes for two weeks and compare them with the schedule.",
    'student_engagement': "Ask your students in a short anonymous poll which activity they enjoyed most and which one least.",
}

# Kept for older code and tests that import it.
ACTIONS = {key: [text] for key, text in FALLBACK_ACTION.items()}


def _topics_for(field, subthemes):
    problems, praise = [], []
    for s in subthemes or []:
        if s.get('category') != field:
            continue
        (problems if s.get('tone') == 'problem' else praise).append(s)
    return problems, praise


def _pick(seed, key, options):
    """Pick one wording. Same seed + key = same wording; other instructors, periods or categories get
    other wording, so the suggestions do not all read like copies of each other."""
    return options[int(hashlib.sha256(f"{seed}|{key}".encode()).hexdigest()[:8], 16) % len(options)]


def _count_text(n):
    return f"{n} comment{'s' if n != 1 else ''}"


def build_rule_suggestions(latest, previous=None, forecast=None, themes=None, subthemes=None, seed=''):
    """
    latest / previous: rows from data.instructor_series() (previous may be None).
    forecast: the dict from forecasting.make_forecast() (may be None or not 'ok').
    themes: list from themes.find_themes() (may be empty).
    subthemes: list from themes.find_subtopics() (may be empty).
    Returns a list of plain dicts, one per category (always all five).
    """
    scores = latest["categories"]
    lowest_field = min(scores, key=scores.get)
    best_field = max(scores, key=scores.get)
    theme_by_key = {t['key']: t for t in (themes or [])}
    predicted = (forecast or {}).get('predicted_categories') if (forecast or {}).get('status') == 'ok' else None
    labels = dict(CATEGORIES)

    items = []
    for field, label in CATEGORIES:
        score = round(scores[field], 2)
        prev_score = round(previous["categories"][field], 2) if previous else None
        change = round(score - prev_score, 2) if prev_score is not None else None
        gap_to_best = round(scores[best_field] - scores[field], 2)
        theme = theme_by_key.get(field)
        negative_theme = bool(theme and theme['negative'] >= conf.MIN_THEME_MENTIONS
                              and theme['negative'] * 2 > theme['mentions'])
        predicted_change = None
        if predicted and field in predicted:
            predicted_change = round(predicted[field]['value'] - score, 2)
        problems, praise = _topics_for(field, subthemes)
        problem_mentions = sum(p['mentions'] for p in problems)
        praise_mentions = sum(p['mentions'] for p in praise)

        reasons = []
        if score < 3.5:
            priority = 'high'
            reasons.append(f"score {score} is below 3.5")
        elif score >= 4.5:
            priority = 'strength'
            reasons.append(f"score {score} is 4.5 or more")
        else:
            priority = 'low'
            if field == lowest_field and scores[lowest_field] < scores[best_field]:
                reasons.append("it is your lowest category")
            if change is not None and change <= -0.2:
                reasons.append(f"it fell by {abs(change)} since the last period")
            if negative_theme:
                reasons.append("most comments about it are negative")
            if predicted_change is not None and predicted_change <= -0.2:
                reasons.append(f"the forecast expects a fall of {abs(predicted_change)}")
            if problems:
                reasons.append(f"{problem_mentions} comments describe a problem here")
            if reasons:
                priority = 'medium'

        # ---- the headline sentence: real numbers only (the wording changes with the seed)
        moved = ''
        if change is not None and change != 0:
            moved = f" ({'up' if change > 0 else 'down'} {abs(change)} since the last period)"
        head = _pick(seed, field + 'head', [
            f"{label} is {score} out of 5{moved}.",
            f"Students rated your {label.lower()} {score} out of 5{moved}.",
            f"Your {label.lower()} score is {score} out of 5{moved}.",
        ])
        if priority != 'strength' and field != best_field and gap_to_best >= 0.2:
            head += f" That is {gap_to_best} below your best category, {labels[best_field]}."

        # ---- the steps: one for every REAL problem topic
        actions = [
            {'topic': p['label'], 'mentions': p['mentions'],
             'text': TOPIC_BY_KEY[p['key']]['action'] if p['key'] in TOPIC_BY_KEY else ''}
            for p in problems
        ]
        actions = [a for a in actions if a['text']]
        praised = [{'topic': p['label'], 'mentions': p['mentions'],
                    'text': TOPIC_BY_KEY[p['key']].get('strength', '')} for p in praise]

        if priority == 'strength':
            body = (f"Students also mention: {', '.join(p['topic'].lower() for p in praised)}. Keep doing this."
                    if praised else _pick(seed, field + 'strong', [
                        "Keep doing what you are doing.",
                        "This is a clear strength. Keep your current habits.",
                        "This is one of your best areas. Share what works with colleagues."]))
        elif actions:
            top = actions[0]
            lead = _pick(seed, field + 'lead', ["Students wrote most about", "The topic students raised most is",
                                                 "Comments focus mainly on"])
            body = f"{lead}: {top['topic'].lower()} ({_count_text(top['mentions'])})."
            if len(actions) > 1:
                body += f" Also: {', '.join(a['topic'].lower() for a in actions[1:])}."
        elif priority in ('high', 'medium'):
            if praise_mentions >= 2:
                body = (f"The comments are mostly positive here ({_count_text(praise_mentions)} praise this), "
                        f"but the score is lower. The two do not match yet.")
                actions = [{'topic': 'Find the reason', 'mentions': 0, 'text': FALLBACK_ACTION[field]}]
            else:
                body = "No clear topic was found in the comments for this category."
                actions = [{'topic': 'Find the reason', 'mentions': 0, 'text': FALLBACK_ACTION[field]}]
        else:
            body = _pick(seed, field + 'fine', [
                "No problem was mentioned in the comments. No change is needed now.",
                "Students did not report a problem here. Keep your routine and watch the next result.",
                "The comments raised no concern in this area, so a small refresh is enough."])

        items.append({
            'category': field,
            'category_label': label,
            'priority': priority,
            'text': f"{head} {body}",
            'source': 'rule',
            'evidence': {
                'score': score,
                'previous_score': prev_score,
                'change': change,
                'gap_to_best': gap_to_best,
                'responses': latest["responses"],
                'period': latest["label"],
                'reasons': reasons,
                'predicted_change': predicted_change,
                'actions': actions,
                'praise': praised,
                'topics': [{'key': s['key'], 'label': s['label'], 'tone': s['tone'], 'mentions': s['mentions']}
                           for s in (problems + praise)],
            },
        })

    order = {'high': 0, 'medium': 1, 'low': 2, 'strength': 3}
    return sorted(items, key=lambda i: (order[i['priority']], i['evidence']['score']))