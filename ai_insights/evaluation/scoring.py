# evaluation/scoring.py
#
# THE ONE PLACE WHERE SCORES ARE CALCULATED
#
# Every page, report and AI file asks this file for averages, so a number can never be different on two
# screens. (Before, the same "Avg of 5 columns" was written by hand in about 15 places.)
#
# THE RULES
#   * Each evaluation has 15 answers (3 per dimension), each 1-5.
#   * Dimension (category) average = the average of its answers.
#     Every evaluation has exactly 3 answers per dimension, so averaging all answers of a dimension equals
#     averaging each student's 3-answer average. Both give the same number.
#   * Overall score = the WEIGHTED mean of the 5 dimension averages:  sum(weight x average) / sum(weight).
#     Weights come from EvaluationCategory.weight of the period's questionnaire (all 1 = plain mean).
#   * Never invent a number: a dimension with no answers is None, and no evaluations means no scores at all.
#
# PRIVACY: only ratings are read. These queries never select the student column.
#
# Every function takes an Evaluation QUERYSET, so each page keeps its own filters
# (one teacher, one period, one department...).

from collections import defaultdict

from django.db.models import Avg, Count, Max, Sum

from .models import Evaluation, EvaluationAnswer, EvaluationCategory, EvaluationPeriod, Questionnaire
from .questionnaire_data import CATEGORY_KEYS

EQUAL_WEIGHTS = {key: 1.0 for key in CATEGORY_KEYS}


# ─────────────────────────────────────────────
# Weights and the overall score
# ─────────────────────────────────────────────
def _weights_of(questionnaire_ids):
    """{questionnaire id: {category key: weight}}. A questionnaire without rows gets equal weights."""
    found = defaultdict(dict)
    for qid, key, weight in EvaluationCategory.objects.filter(
            questionnaire_id__in=list(questionnaire_ids)).values_list('questionnaire_id', 'key', 'weight'):
        found[qid][key] = float(weight)
    return {qid: {**EQUAL_WEIGHTS, **found.get(qid, {})} for qid in questionnaire_ids}


def current_weights():
    current = Questionnaire.current()
    return _weights_of([current.pk])[current.pk] if current else dict(EQUAL_WEIGHTS)


def overall_of(categories, weights=None):
    """
    Weighted mean of the dimension averages (a dictionary {key: average or None}).
    Dimensions that are None are left out; no dimension at all gives None.
    """
    weights = weights or EQUAL_WEIGHTS
    pairs = [(float(v), weights.get(k, 1.0)) for k, v in categories.items() if v is not None and k in CATEGORY_KEYS]
    total_weight = sum(w for _, w in pairs)
    if not pairs or total_weight <= 0:
        return None
    return sum(v * w for v, w in pairs) / total_weight


# ─────────────────────────────────────────────
# Averages of many evaluations
# ─────────────────────────────────────────────
def _answers(evaluations):
    return EvaluationAnswer.objects.filter(evaluation__in=evaluations.order_by().values('pk'))


def category_means(evaluations):
    """{category key: average or None} for all 5 dimensions (one query)."""
    rows = _answers(evaluations).values('question__category__key').annotate(avg=Avg('rating'))
    found = {r['question__category__key']: float(r['avg']) for r in rows}
    return {key: found.get(key) for key in CATEGORY_KEYS}


def summary(evaluations):
    """
    {'responses': n, 'categories': {key: avg or None}, 'overall': x or None}
    No evaluations: responses 0, every average None.
    """
    responses = evaluations.order_by().count()
    if not responses:
        return {'responses': 0, 'categories': {key: None for key in CATEGORY_KEYS}, 'overall': None}
    categories = category_means(evaluations)
    return {'responses': responses, 'categories': categories,
            'overall': overall_of(categories, current_weights())}


def grouped(evaluations, *group_fields):
    """
    The same numbers for every group at once, with a fixed number of queries (no query per teacher).
      grouped(Evaluation.objects.all(), 'teacher_id')                 -> {(teacher_id,): {...}}
      grouped(qs, 'teacher_id', 'period_id')                          -> {(teacher_id, period_id): {...}}
    When 'period_id' is one of the groups, each period is weighted with ITS OWN questionnaire.
    """
    base = evaluations.order_by()
    counts = {tuple(r[f] for f in group_fields): r['n']
              for r in base.values(*group_fields).annotate(n=Count('id'))}
    if not counts:
        return {}

    prefixed = [f'evaluation__{f}' for f in group_fields]
    rows = (_answers(evaluations).values(*prefixed, 'question__category__key').annotate(avg=Avg('rating')))
    cats = defaultdict(dict)
    for r in rows:
        cats[tuple(r[f] for f in prefixed)][r['question__category__key']] = float(r['avg'])

    if 'period_id' in group_fields:
        at = group_fields.index('period_id')
        period_ids = {k[at] for k in counts}
        questionnaire_of = dict(EvaluationPeriod.objects.filter(pk__in=period_ids).values_list('pk', 'questionnaire_id'))
        weights_of = _weights_of({q for q in questionnaire_of.values() if q})
        weights_for = lambda key: weights_of.get(questionnaire_of.get(key[at]), EQUAL_WEIGHTS)
    else:
        shared = current_weights()
        weights_for = lambda key: shared

    result = {}
    for key, n in counts.items():
        categories = {k: cats[key].get(k) for k in CATEGORY_KEYS}
        result[key] = {'responses': n, 'categories': categories,
                       'overall': overall_of(categories, weights_for(key))}
    return result


def period_summary(teacher, period):
    """summary() of ONE instructor in ONE period, or None when nobody has evaluated yet."""
    data = grouped(Evaluation.objects.filter(teacher=teacher, period=period), 'period_id')
    return data.get((period.pk,))


# ─────────────────────────────────────────────
# One single evaluation (the student's own history page)
# ─────────────────────────────────────────────
def evaluation_categories(evaluation):
    """{key: average of this student's answers in that dimension or None} for ONE evaluation."""
    rows = (EvaluationAnswer.objects.filter(evaluation=evaluation)
            .values('question__category__key').annotate(avg=Avg('rating')))
    found = {r['question__category__key']: float(r['avg']) for r in rows}
    return {key: found.get(key) for key in CATEGORY_KEYS}


def evaluation_overall(evaluation):
    """Overall score of ONE evaluation, rounded to 2 decimals (None when it has no answers)."""
    weights = _weights_of([evaluation.period.questionnaire_id]).get(evaluation.period.questionnaire_id, EQUAL_WEIGHTS) \
        if evaluation.period.questionnaire_id else EQUAL_WEIGHTS
    value = overall_of(evaluation_categories(evaluation), weights)
    return None if value is None else round(value, 2)


def overall_for_evaluations(evaluation_ids):
    """{evaluation id: overall score} for many evaluations with a fixed number of queries."""
    ids = list(evaluation_ids)
    if not ids:
        return {}
    rows = (EvaluationAnswer.objects.filter(evaluation_id__in=ids)
            .values('evaluation_id', 'question__category__key').annotate(avg=Avg('rating')))
    cats = defaultdict(dict)
    for r in rows:
        cats[r['evaluation_id']][r['question__category__key']] = float(r['avg'])
    questionnaire_of = dict(Evaluation.objects.filter(pk__in=ids).values_list('pk', 'period__questionnaire_id'))
    weights_of = _weights_of({q for q in questionnaire_of.values() if q})
    out = {}
    for eid in ids:
        value = overall_of({k: cats[eid].get(k) for k in CATEGORY_KEYS},
                           weights_of.get(questionnaire_of.get(eid), EQUAL_WEIGHTS))
        out[eid] = None if value is None else round(value, 2)
    return out


# ─────────────────────────────────────────────
# "Did anything change?" code for the AI (see ai_insights/services/analysis.py)
# ─────────────────────────────────────────────
def fingerprint_parts(periods):
    """Counts and sums of the real answers of these periods: they change when any rating or evaluation changes."""
    answers = EvaluationAnswer.objects.filter(evaluation__period__in=periods).aggregate(
        n=Count('id'), total=Sum('rating'), newest=Max('evaluation_id'))
    evaluations = Evaluation.objects.filter(period__in=periods).aggregate(n=Count('id'), newest=Max('id'))
    return sorted({**{f'answers_{k}': v for k, v in answers.items()},
                   **{f'evaluations_{k}': v for k, v in evaluations.items()}}.items())
