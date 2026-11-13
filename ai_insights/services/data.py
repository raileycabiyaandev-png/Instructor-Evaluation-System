# ai_insights/services/data.py
#
# Reads REAL evaluation data. It reuses the helpers that already exist in
# users/views.py, so "finished period" means exactly the same thing
# everywhere in the system.

import hashlib
import json

from django.db.models import Avg, Count
from django.utils import timezone

from evaluation.models import Evaluation, EvaluationPeriod

from .. import conf

# The five required categories (database field, label).
CATEGORIES = [
    ("teaching_effectiveness", "Teaching Effectiveness"),
    ("communication_skills", "Communication Skills"),
    ("classroom_management", "Classroom Management"),
    ("punctuality", "Punctuality"),
    ("student_engagement", "Student Engagement"),
]
CATEGORY_LABELS = dict(CATEGORIES)

TERM_POSITION = {'prelims': 1, 'midterm': 2, 'prefinals': 3, 'finals': 4}

# Plain-words name of each forecasting method (kept here so page loads never import scikit-learn)
MODEL_TEXT = {
    'last': 'Same as the last period',
    'mean': 'Average of all past periods',
    'trend': 'Straight-line trend over time (Ridge regression)',
    'trend_term': 'Trend over time + which term of the semester (Ridge regression)',
}


def period_label(period):
    return f"{period.get_term_display()} · {period.get_semester_display()}, S.Y. {period.school_year}"


def finished_periods():
    """All finished periods, oldest first (same rule as the teacher pages)."""
    from users.views import _finished_q, period_sort_key   # reuse, do not copy

    today = timezone.now().date()
    return sorted(EvaluationPeriod.objects.filter(_finished_q(today)), key=period_sort_key)


def sort_key(period):
    """Oldest -> newest (school year, semester, term). Same rule as the teacher pages."""
    from users.views import period_sort_key
    return period_sort_key(period)


def periods_up_to(period):
    """Finished periods from the oldest up to AND including `period`."""
    return [p for p in finished_periods() if sort_key(p) <= sort_key(period)]


def analyse_history(instructor, periods=None):
    """
    Checks the REAL history of one instructor and returns (series, quality).

    series  - one row per finished period that has at least MIN_RESPONSES student responses
              and no missing scores (fewer responses is too noisy to learn from). Oldest first.
    quality - what was found and what was left out, so the admin can see WHY:
              {"periods_found": 5, "periods_used": 4, "total_responses": 62,
               "skipped": [{"label": ..., "responses": 2, "reason": ...}]}
    """
    periods = finished_periods() if periods is None else periods
    by_id = {p.pk: p for p in periods}

    rows = (
        Evaluation.objects
        .filter(teacher=instructor, period_id__in=by_id.keys())
        .values('period_id')
        .annotate(responses=Count('id'), **{f: Avg(f) for f, _ in CATEGORIES})
    )
    found = {r['period_id']: r for r in rows}

    series, skipped = [], []
    for period in periods:                      # keeps oldest -> newest order
        r = found.get(period.pk)
        if not r:
            continue                            # no responses at all: nothing to report
        if r['responses'] < conf.MIN_RESPONSES:
            skipped.append({"label": period_label(period), "responses": r['responses'],
                            "reason": f"fewer than {conf.MIN_RESPONSES} responses"})
            continue
        if any(r[f] is None for f, _ in CATEGORIES):
            skipped.append({"label": period_label(period), "responses": r['responses'],
                            "reason": "missing scores"})
            continue
        cats = {f: round(float(r[f]), 4) for f, _ in CATEGORIES}
        series.append({
            "period": period,
            "label": period_label(period),
            "responses": r['responses'],
            "overall": round(sum(cats.values()) / len(cats), 4),
            "categories": cats,
        })
    quality = {
        "periods_found": len(series) + len(skipped),
        "periods_used": len(series),
        "total_responses": sum(s["responses"] for s in series),
        "skipped": skipped,
    }
    return series, quality


# ─────────────────────────────────────────────
# ONE period of ONE instructor (used by the teacher Dashboard, the Results page and the report,
# so all three always show the same numbers).
# ─────────────────────────────────────────────
ATTENTION_BELOW = 3.5      # a category under this score "needs attention"
ATTENTION_DROP = 0.2       # ... or one that fell this much since the previous period


def score_level(value):
    """Colour rule used everywhere: 4.5+ high, 3.5+ mid, below that low."""
    return 'high' if value >= 4.5 else 'mid' if value >= 3.5 else 'low'


def indicator_for(diff):
    """'improving' / 'declining' / 'stable' from the change in the overall score."""
    if diff is None:
        return None
    if diff >= conf.STEADY_BAND:
        return 'improving'
    if diff <= -conf.STEADY_BAND:
        return 'declining'
    return 'stable'


def expected_students(instructor, period):
    """
    How many students may evaluate this instructor in this period: the active students of the
    sections he or she teaches in the period's school year and semester (each student counted once).
    """
    from academics.models import TeachingAssignment
    from users.models import User

    section_ids = TeachingAssignment.objects.filter(
        teacher=instructor, school_year=period.school_year, semester=period.semester,
    ).values_list('section_id', flat=True)
    return User.objects.filter(role='student', is_active=True, section_id__in=list(section_ids)).count()


def _period_scores(instructor, period):
    agg = Evaluation.objects.filter(teacher=instructor, period=period).aggregate(
        total=Count('id'), **{f: Avg(f) for f, _ in CATEGORIES})
    if not agg['total']:
        return None
    cats = {f: round(float(agg[f]), 2) for f, _ in CATEGORIES}
    return {'responses': agg['total'], 'categories': cats, 'overall': round(sum(cats.values()) / len(cats), 2)}


def period_snapshot(instructor, period):
    """
    Everything the pages need about ONE period of ONE instructor. Only counts and averages are read
    (never a student). Returns a plain dict. 'has_data' is False when nobody has answered yet.
    The comparison is with the newest EARLIER finished period that has answers.
    """
    scores = _period_scores(instructor, period)
    expected = expected_students(instructor, period)
    responses = scores['responses'] if scores else 0
    snap = {
        'period': period, 'label': period_label(period), 'has_data': scores is not None,
        'responses': responses, 'expected': expected,
        'percent': min(100, round(responses / expected * 100)) if expected else 0,
        'low_sample': 0 < responses < conf.MIN_RESPONSES,
        'overall': scores['overall'] if scores else None,
        'previous': None, 'change': None, 'indicator': None,
        'categories': [], 'strongest': None, 'weakest': None, 'attention': [],
    }
    if not scores:
        return snap

    previous = None
    for p in reversed([x for x in finished_periods() if sort_key(x) < sort_key(period)]):
        found = _period_scores(instructor, p)
        if found:
            previous = {'period': p, 'label': period_label(p), **found}
            break
    if previous:
        snap['previous'] = previous
        snap['change'] = round(scores['overall'] - previous['overall'], 2)
        snap['indicator'] = indicator_for(snap['change'])

    rows = []
    for field, label in CATEGORIES:
        score = scores['categories'][field]
        row = {'key': field, 'label': label, 'score': score, 'percent': round(score / 5 * 100),
               'level': score_level(score), 'previous': None, 'previous_percent': 0, 'diff': None}
        if previous:
            row['previous'] = previous['categories'][field]
            row['previous_percent'] = round(row['previous'] / 5 * 100)
            row['diff'] = round(score - row['previous'], 2)
        rows.append(row)
    snap['categories'] = rows
    snap['strongest'] = max(rows, key=lambda r: r['score'])
    snap['weakest'] = min(rows, key=lambda r: r['score'])
    # "Needs attention": a low score, or a clear drop since the previous period. Worst first.
    snap['attention'] = sorted(
        [r for r in rows if r['score'] < ATTENTION_BELOW or (r['diff'] is not None and r['diff'] <= -ATTENTION_DROP)],
        key=lambda r: r['score'])
    return snap


def history_rows(instructor, upto=None):
    """
    The COMPLETE real history of one instructor: every finished period with at least one answer,
    oldest first, with the change from the period before. (Unlike analyse_history, periods with few
    responses are kept here and marked 'low_sample', because this is for reading, not for the forecast.)
    """
    rows, before = [], None
    for p in finished_periods():
        if upto is not None and sort_key(p) > sort_key(upto):
            continue
        found = _period_scores(instructor, p)
        if not found:
            continue
        diff = None if before is None else round(found['overall'] - before, 2)
        rows.append({'period': p, 'label': period_label(p), 'responses': found['responses'],
                     'overall': found['overall'], 'categories': found['categories'],
                     'diff': diff, 'indicator': indicator_for(diff), 'low_sample': found['responses'] < conf.MIN_RESPONSES})
        before = found['overall']
    return rows


def instructor_series(instructor, periods=None):
    """Just the usable history (see analyse_history)."""
    return analyse_history(instructor, periods)[0]


def hash_of(*parts):
    """Short fingerprint of the inputs. Same inputs = same hash = skip the work."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()