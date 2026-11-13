# ai_insights/services/school.py
#
# SCHOOL-LEVEL facts and text for ONE evaluation period (admin only).
#
#   facts_for(period)     real numbers only: school average, category averages, comment counts,
#                         topic counts, number of alerts. No comment text, no instructor names.
#   rule_text(facts)      plain, visible rules -> summary, strengths, weaknesses, suggestions
#   generate(period, ...) saves a SchoolInsight. Ollama writes the text when it is on and its answer
#                         passes the safety checks; otherwise the rule text is saved. Each item says which.
#
# The text never invents a number: every figure comes from `facts`.

import logging

from django.db.models import Avg, Count
from django.utils import timezone

from evaluation.models import Evaluation

from .. import conf
from ..models import Alert, FeedbackSummary, SchoolInsight
from . import ollama
from .data import CATEGORIES, period_label

log = logging.getLogger(__name__)

TIPS = {
    'teaching_effectiveness': "Hold a short teaching workshop on explaining lessons clearly, with examples and a steady pace.",
    'communication_skills': "Remind instructors to answer student questions and to be easy to approach, for example with set consultation hours.",
    'classroom_management': "Share good classroom-management practices between instructors and agree on clear, fair class rules.",
    'punctuality': "Remind instructors to start and end classes on time and to announce schedule changes early.",
    'student_engagement': "Encourage more activities and group work, and let instructors share lesson ideas that keep students interested.",
}
WEAK_BELOW = 3.5          # a category average below this is called weak in the text


def _topics(rows, tone):
    counts = {}
    for row in rows:
        for sub in row.subthemes or []:
            if sub.get('tone') != tone:
                continue
            c = counts.setdefault(sub['key'], {'key': sub['key'], 'label': sub['label'], 'mentions': 0, 'instructors': 0})
            c['mentions'] += sub.get('mentions', 0)
            c['instructors'] += 1
    return sorted(counts.values(), key=lambda c: (-c['mentions'], c['key']))[:5]


def facts_for(period):
    """Real numbers for the whole school in one period. Returns None when nobody answered."""
    from users import dashboard_data

    evals = Evaluation.objects.filter(period=period, teacher__role='instructor')
    total = evals.aggregate(
        n=Count('id'), a1=Avg('teaching_effectiveness'), a2=Avg('communication_skills'),
        a3=Avg('classroom_management'), a4=Avg('punctuality'), a5=Avg('student_engagement'))
    if not total['n']:
        return None
    scores = [total['a1'], total['a2'], total['a3'], total['a4'], total['a5']]
    categories = [{'key': key, 'label': label, 'score': round(score, 2)}
                  for (key, label), score in zip(CATEGORIES, scores)]
    average = round(sum(scores) / 5, 2)
    instructors = evals.values('teacher_id').distinct().count()

    block = dashboard_data.school_block(period, 'closed')
    rows = list(FeedbackSummary.objects.filter(period=period, hidden=False))
    comments = {
        'analysed': sum(r.n_analyzed for r in rows),
        'positive': sum(r.positive for r in rows),
        'neutral': sum(r.neutral for r in rows),
        'negative': sum(r.negative for r in rows),
        'instructors': len(rows),
    }
    ranked = sorted(categories, key=lambda c: (-c['score'], c['key']))
    return {
        'period': period_label(period), 'responses': total['n'], 'instructors': instructors,
        'average': average, 'categories': categories,
        'strongest': ranked[0] if ranked[0]['score'] > ranked[-1]['score'] else None,
        'weakest': ranked[-1] if ranked[0]['score'] > ranked[-1]['score'] else None,
        'previous': block.get('previous'), 'change': block.get('change'), 'trend': block.get('trend'),
        'comments': comments,
        'praise': _topics(rows, 'praise'), 'problems': _topics(rows, 'problem'),
        'alerts_open': Alert.objects.filter(period=period).exclude(status='resolved').count(),
    }


def facts_text(f):
    """The ONLY text Ollama sees: numbers and topic counts."""
    lines = [
        f"Period: {f['period']}. Evaluations: {f['responses']} from {f['instructors']} instructor(s).",
        f"School average: {f['average']:.2f} out of 5.",
    ]
    if f.get('previous') and f.get('change') is not None:
        lines.append(f"School average in the period before: {f['previous']['average']:.2f}. "
                     f"Change: {f['change']:+.2f} ({f['trend']}).")
    lines.append("Category averages: " + "; ".join(f"{c['label']} {c['score']:.2f}" for c in f['categories']) + ".")
    c = f['comments']
    if c['analysed']:
        lines.append(f"Comments analysed: {c['analysed']} (positive {c['positive']}, neutral {c['neutral']}, "
                     f"negative {c['negative']}).")
    if f['praise']:
        lines.append("Things students like (comments): " + "; ".join(f"{t['label']} ({t['mentions']})" for t in f['praise']) + ".")
    if f['problems']:
        lines.append("Things students want improved (comments): " + "; ".join(f"{t['label']} ({t['mentions']})" for t in f['problems']) + ".")
    lines.append(f"Instructors with an open performance alert: {f['alerts_open']}.")
    return "\n".join(lines)


def rule_text(f):
    """Plain rules. Same facts in, same text out."""
    parts = [f"In the {f['period']} the school average is {f['average']:.2f} out of 5, "
             f"from {f['responses']} evaluation{'s' if f['responses'] != 1 else ''} of {f['instructors']} instructor{'s' if f['instructors'] != 1 else ''}."]
    if f.get('previous') and f.get('change') is not None:
        word = {'improving': 'higher than', 'declining': 'lower than', 'stable': 'about the same as'}.get(f['trend'], 'compared with')
        parts.append(f"This is {word} the period before ({f['previous']['average']:.2f}, change {f['change']:+.2f}).")
    if f['strongest'] and f['weakest']:
        parts.append(f"{f['strongest']['label']} is the strongest category ({f['strongest']['score']:.2f}) and "
                     f"{f['weakest']['label']} is the weakest ({f['weakest']['score']:.2f}).")
    c = f['comments']
    if c['analysed']:
        parts.append(f"Of {c['analysed']} analysed comments, {c['positive']} were positive, "
                     f"{c['neutral']} neutral and {c['negative']} negative.")
    else:
        parts.append("There were not enough comments to analyse.")

    strengths, weaknesses, suggestions = [], [], []
    if f['strongest']:
        strengths.append(f"{f['strongest']['label']} has the highest school average ({f['strongest']['score']:.2f}).")
    for t in f['praise'][:2]:
        strengths.append(f"Students like: {t['label'].lower()} ({t['mentions']} comment{'s' if t['mentions'] != 1 else ''}).")

    weak = [c_ for c_ in f['categories'] if c_['score'] < WEAK_BELOW]
    if f['weakest']:
        weaknesses.append(f"{f['weakest']['label']} has the lowest school average ({f['weakest']['score']:.2f}).")
    for c_ in weak:
        if not f['weakest'] or c_['key'] != f['weakest']['key']:
            weaknesses.append(f"{c_['label']} is below {WEAK_BELOW:g} ({c_['score']:.2f}).")
    for t in f['problems'][:2]:
        weaknesses.append(f"Students want improved: {t['label'].lower()} ({t['mentions']} comment{'s' if t['mentions'] != 1 else ''}).")

    if f['weakest']:
        suggestions.append({'label': f['weakest']['label'], 'text': TIPS[f['weakest']['key']]})
    for c_ in weak:
        if not f['weakest'] or c_['key'] != f['weakest']['key']:
            suggestions.append({'label': c_['label'], 'text': TIPS[c_['key']]})
    if f['alerts_open']:
        suggestions.append({'label': 'Follow up',
                            'text': f"Look at the {f['alerts_open']} open performance alert{'s' if f['alerts_open'] != 1 else ''} and talk with those instructors."})
    return {'summary': ' '.join(parts), 'strengths': strengths, 'weaknesses': weaknesses, 'suggestions': suggestions}


def generate(period, use_llm=False, run=None):
    """Make and save the school insight of `period`. Returns it, or None when nobody answered."""
    f = facts_for(period)
    if f is None:
        SchoolInsight.objects.filter(period=period).delete()
        return None

    rules = rule_text(f)
    summary, summary_source = rules['summary'], 'rule'
    strengths = [{'text': t, 'source': 'rule'} for t in rules['strengths']]
    weaknesses = [{'text': t, 'source': 'rule'} for t in rules['weaknesses']]
    suggestions = [{'text': s['text'], 'label': s['label'], 'source': 'rule'} for s in rules['suggestions']]
    llm_model = ''

    if use_llm:
        try:
            ai = ollama.school_overview(facts_text(f))
            summary, summary_source, llm_model = ai['summary'], 'ollama', conf.ollama_model()
            if ai['strengths']:
                strengths = [{'text': t, 'source': 'ollama'} for t in ai['strengths']]
            if ai['weaknesses']:
                weaknesses = [{'text': t, 'source': 'ollama'} for t in ai['weaknesses']]
            if ai['suggestions']:
                suggestions = [{'text': t, 'label': '', 'source': 'ollama'} for t in ai['suggestions']]
        except ollama.OllamaUnavailable as exc:
            log.info("School overview: Ollama not used (%s)", exc)

    insight, _ = SchoolInsight.objects.update_or_create(
        period=period,
        defaults=dict(run=run, summary_text=summary, summary_source=summary_source, strengths=strengths,
                      weaknesses=weaknesses, suggestions=suggestions, facts=f, llm_model=llm_model,
                      generated_at=timezone.now()))
    return insight