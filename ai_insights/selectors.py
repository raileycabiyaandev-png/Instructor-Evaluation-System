# ai_insights/selectors.py
#
# READ-ONLY helpers used by the dashboards. They only read SAVED results.
# They never run a model, so a page always opens fast, and they never raise:
# a problem becomes a small "unavailable" message on the page.

import logging
import re

from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q

from evaluation.models import Evaluation
from users.models import User

from . import conf
from . import charts
from .filters import filtered_instructors, term_value
from .models import (Alert, AlertSettings, AnalysisRun, FeedbackSummary, ForecastResult,
                     InstructorNotice, SchoolInsight, Suggestion)
from .services.data import (CATEGORIES, CATEGORY_LABELS, MODEL_TEXT, analyse_history, finished_periods,
                            period_label, periods_up_to, sort_key)

log = logging.getLogger(__name__)

PRIORITY_TEXT = {
    'high': 'Needs attention', 'medium': 'Can improve', 'low': 'Small idea', 'strength': 'Strength',
}


def latest_release():
    """
    The newest FINISHED official analysis (or None). Instructors see AI results only after one exists.
    "Newest" = the latest evaluation period, then the highest version.
    """
    runs = list(AnalysisRun.objects.filter(status__in=AnalysisRun.RELEASED).select_related('period'))
    return max(runs, key=lambda r: (sort_key(r.period), r.version), default=None)


def _only_released(rows, cutoff, period_field):
    """
    Keep only what an instructor may see: results made before the last official release, PLUS any result
    about a period that release already covers. The second part matters when the administrator presses
    "Regenerate": the saved rows are rewritten in place (new time stamp) while the run is still working or
    if it fails, and without it the instructor's results would vanish.
    Results about a NEWER period than the release stay hidden until the next official run.
    """
    if cutoff is None:
        return rows
    query = Q(generated_at__lte=cutoff) if period_field != 'notice' else Q(created_at__lte=cutoff)
    release = latest_release()
    if release is not None:
        covered = [p.pk for p in periods_up_to(release.period)]
        query |= Q(**{(('period' if period_field == 'notice' else period_field) + '__in'): covered})
    return rows.filter(query)


def _newest_by_period(rows):
    """Of several saved forecast rows, the one based on the newest period (rows without a period are oldest)."""
    return max(rows, key=lambda r: (sort_key(r.based_on_period) if r.based_on_period_id else ('', '', -1),
                                    r.generated_at), default=None)


def _forecast_block(instructor, cutoff=None):
    """The instructor's newest saved forecast row. `cutoff`: ignore rows made after the last official release."""
    rows = ForecastResult.objects.filter(instructor=instructor).select_related('based_on_period')
    rows = _only_released(rows, cutoff, 'based_on_period')
    row = _newest_by_period(list(rows))
    if row is None:
        return {'state': 'not_generated'}

    quality = row.data_quality or {}
    if row.status != 'ok':
        return {'state': row.status, 'message': row.message, 'generated_at': row.generated_at,
                'periods_used': row.periods_used, 'quality': quality, 'history': row.history or [],
                'based_on_label': period_label(row.based_on_period) if row.based_on_period_id else ''}

    cats = [
        {**row.predicted_categories[f], 'key': f}
        for f, _ in CATEGORIES if f in row.predicted_categories
    ]
    history = row.history or []
    chart = {
        'labels': [h['label'] for h in history] + [row.target_label or 'Next period'],
        'actual': [h['overall'] for h in history] + [None],
        # the dashed line starts at the last REAL score so the eye can follow it
        'predicted': [None] * (len(history) - 1) + [history[-1]['overall'] if history else None,
                                                    row.predicted_overall],
        'low': row.range_low, 'high': row.range_high,
    }
    last_actual = history[-1]['overall'] if history else None
    return {
        'state': 'ok', 'row': row, 'categories': cats, 'chart': chart, 'last_actual': last_actual,
        'change': None if last_actual is None else round(row.predicted_overall - last_actual, 2),
        'beats_baseline': (row.validation_mae is not None and row.baseline_mae is not None
                           and row.validation_mae < row.baseline_mae),
        'generated_at': row.generated_at, 'quality': quality,
        'model_text': MODEL_TEXT.get(row.model_name, row.model_name),
        'based_on_label': period_label(row.based_on_period) if row.based_on_period_id else '',
    }


def _donut(positive, neutral, negative):
    """CSS ring for the sentiment chart (no chart library needed, so it always draws)."""
    total = positive + neutral + negative
    if not total:
        return ''
    p = round(positive / total * 100)
    n = round(neutral / total * 100)
    return (f"conic-gradient(#2f7d4f 0 {p}%, #9aa9cf {p}% {min(p + n, 100)}%, "
            f"#b4532a {min(p + n, 100)}% 100%)")


def _topic_rows(subthemes):
    """Topics with a bar width that is relative to the biggest topic."""
    biggest = max((s['mentions'] for s in subthemes), default=1)
    return [{**s, 'width': max(8, round(s['mentions'] / biggest * 100))} for s in subthemes]


def _feedback_block(instructor, period, cutoff=None):
    rows = FeedbackSummary.objects.filter(instructor=instructor, period=period)
    rows = _only_released(rows, cutoff, 'period')
    row = rows.first()
    if row is None:
        return {'state': 'not_generated'}
    if row.hidden:
        return {'state': 'hidden', 'reason': row.hidden_reason}
    total = row.positive + row.neutral + row.negative

    def pct(n):
        return round(n / total * 100) if total else 0

    subs = row.subthemes or []
    return {
        'state': 'ok', 'row': row, 'model_note': conf.sentiment_note(),
        'ai': row.summary_source == 'ollama',
        'donut': _donut(row.positive, row.neutral, row.negative),
        'bars': [
            {'label': 'Positive', 'key': 'positive', 'count': row.positive, 'percent': pct(row.positive)},
            {'label': 'Neutral', 'key': 'neutral', 'count': row.neutral, 'percent': pct(row.neutral)},
            {'label': 'Negative', 'key': 'negative', 'count': row.negative, 'percent': pct(row.negative)},
        ],
        'problems': _topic_rows([s for s in subs if s.get('tone') == 'problem']),
        'praise': _topic_rows([s for s in subs if s.get('tone') == 'praise']),
        'categories': _topic_rows(row.themes or []),
        'positive_points': row.positive_points or [],
        'negative_points': row.negative_points or [],
    }


def _suggestion_items(rows):
    """Merge the rule rows and the AI rows of ONE instructor/period into one card per category."""
    rules = {r.category: r for r in rows if r.source == 'rule'}
    ai = {r.category: r for r in rows if r.source == 'ollama' and r.category != 'overall'}
    order = {'high': 0, 'medium': 1, 'low': 2, 'strength': 3}
    items = []
    for key, rule in rules.items():
        e = rule.evidence or {}
        written = ai.get(key)
        score = e.get('score')
        items.append({
            'key': key, 'label': rule.category_label, 'priority': rule.priority,
            'priority_text': PRIORITY_TEXT.get(rule.priority, rule.priority),
            'text': written.text if written else rule.text, 'rule_text': rule.text,
            'ai': written is not None, 'llm_model': written.llm_model if written else '',
            'score': score, 'score_width': round((score or 0) / 5 * 100), 'change': e.get('change'),
            'actions': e.get('actions', []), 'praise': e.get('praise', []), 'reasons': e.get('reasons', []),
        })
    items.sort(key=lambda i: (order.get(i['priority'], 9), i['score'] if i['score'] is not None else 5))
    return items


def _suggestion_block(instructor, period, cutoff=None):
    found = Suggestion.objects.filter(instructor=instructor, period=period)
    found = _only_released(found, cutoff, 'period')
    rows = list(found.order_by('id'))
    if not rows:
        return {'state': 'not_generated'}
    items = _suggestion_items(rows)
    note = next((r for r in rows if r.source == 'ollama' and r.category == 'overall'), None)
    counts = {k: sum(1 for i in items if i['priority'] == k) for k in PRIORITY_TEXT}
    return {'state': 'ok', 'items': items, 'note': note, 'counts': counts,
            'ai_count': sum(1 for i in items if i['ai'])}


def _latest_period(instructor, periods=None):
    for p in reversed(finished_periods() if periods is None else periods):
        if Evaluation.objects.filter(teacher=instructor, period=p).exists():
            return p
    return None


def _is_latest(instructor, period):
    latest = _latest_period(instructor)
    return latest is not None and latest.pk == period.pk


NOT_RELEASED_TEXT = ("The official AI analysis has not been generated yet by your administrator. "
                     "It will appear here automatically as soon as it is ready.")


def _not_included(block, release, instructor=None, period=None):
    """
    After an official analysis exists, a block can still be empty. Say the TRUE reason, not "not generated yet".
    """
    if block.get('state') != 'not_generated':
        return block
    covered = period_label(release.period)
    had_responses = instructor is not None and Evaluation.objects.filter(
        teacher=instructor, period=release.period).exists()
    if instructor is not None and not had_responses:
        text = (f"The official analysis for {covered} did not include you, because you had no responses in that "
                f"period. You will be included after you receive responses in a period that the administrator analyses.")
    elif period is not None and period.pk != release.period_id:
        text = (f"The administrator's latest analysis is for {covered}. Nothing was saved yet for "
                f"{period_label(period)}. It will appear after the administrator analyses that period.")
    else:
        text = (f"The analysis for {covered} ran, but nothing was saved for you here. For a feedback summary, "
                f"at least {conf.MIN_COMMENTS} students must have written a comment. Ask your administrator to run "
                f"the analysis again if you think this is wrong.")
    return {'state': 'not_included', 'message': text}


def _release_info(release):
    return {'version': release.version, 'period_label': period_label(release.period),
            'finished_at': release.finished_at, 'engine_version': release.engine_version}


def _notices(instructor, cutoff):
    """The instructor's own notices made by analyses that are already official (released)."""
    rows = list(_only_released(InstructorNotice.objects.filter(instructor=instructor), cutoff, 'notice')
                .select_related('period')[:6])
    return {'items': rows, 'unread': sum(1 for r in rows if r.read_at is None)}


def progress_chart(labels, values, live=None, forecast=None):
    """
    The teacher's "progress over time" line chart (drawn on the server, so it always shows).
      labels, values : real scores of FINISHED periods, oldest first
      live           : (label, value) the open period so far (dashed, not final)
      forecast       : (label, value) the AI prediction (dashed, never mixed up with a real score)
    """
    labels = [_short_label(x) for x in labels]
    real = list(values)
    extra = []                                    # dashed lines: start on the last real score
    if live is not None:
        extra.append(('Now, so far (not final)', live))
    if forecast is not None:
        extra.append(('AI prediction (not a real score)', forecast))
    series = [{'name': 'Real score', 'values': real, 'color': '#1b2f6b'}]
    n_real = len(real)
    for name, (label, value) in extra:
        labels.append(_short_label(label))
        for s_ in series:
            s_['values'].append(None)
    total = len(labels)
    for name, (label, value) in extra:
        idx = labels.index(_short_label(label))
        dashed = [None] * total
        if n_real:
            dashed[n_real - 1] = real[n_real - 1]
        dashed[idx] = value
        series.append({'name': name, 'values': dashed, 'color': '#c9a227', 'dashed': True})
    chart = charts.line_chart(labels, series)
    if chart:
        for line in chart['lines'][1:]:           # the first dot of a dashed line is the last REAL score
            if len(line['dots']) > 1 and n_real:
                line['dots'] = line['dots'][1:]
    return chart


def _teacher_ai(instructor):
    """
    The saved AI results of the teacher Dashboard. Own data only (the caller passes request.user).
    Nothing is shown until the administrator has finished an official analysis.
    """
    base = {'error': False, 'min_periods': conf.MIN_PERIODS, 'min_responses': conf.MIN_RESPONSES}
    try:
        release = latest_release()
        if release is None:
            return {**base, 'released': False, 'release': None, 'latest_label': '',
                    'forecast': {'state': 'not_released', 'message': NOT_RELEASED_TEXT},
                    'feedback': {'state': 'not_released'}, 'suggestions': {'state': 'not_released'},
                    'notices': {'items': [], 'unread': 0}}
        cutoff = release.finished_at
        latest = _latest_period(instructor)

        def guarded(name, make):
            """One block that fails must not empty the others. The reason goes to the server log."""
            try:
                return make()
            except Exception:
                log.exception("Could not read the AI %s of instructor %s", name, instructor.pk)
                return {'state': 'error', 'message': 'This part could not be loaded. Please tell your administrator.'}

        return {
            **base, 'released': True, 'release': _release_info(release),
            'notices': guarded('notices', lambda: _notices(instructor, cutoff)),
            'forecast': guarded('forecast', lambda: _not_included(_forecast_block(instructor, cutoff), release, instructor)),
            'latest_label': period_label(latest) if latest else '',
            'feedback': (guarded('feedback', lambda: _not_included(_feedback_block(instructor, latest, cutoff), release, instructor, latest))
                         if latest else {'state': 'no_period'}),
            'suggestions': (guarded('suggestions', lambda: _not_included(_suggestion_block(instructor, latest, cutoff), release, instructor, latest))
                            if latest else {'state': 'no_period'}),
        }
    except Exception:
        log.exception("Could not read AI results")
        return {'error': True}


def _short_label(text):
    """'Midterm · Second Semester, S.Y. 2024-2025' -> 'Midterm · 2nd Sem 24-25' (fits under a chart)."""
    text = (text or '').replace('First Semester', '1st Sem').replace('Second Semester', '2nd Sem')
    text = text.replace('same semester', 'same sem').replace('next semester', 'next sem')
    return re.sub(r',?\s*S\.Y\.\s*\d{2}(\d{2})-\d{2}(\d{2})', r' \1-\2', text)


def teacher_dashboard_ai(instructor):
    """
    Everything the teacher Dashboard and Evaluation Results pages need from the AI (own data only, released
    data only). Adds a server-drawn forecast chart (it always draws, no script needed) and the progress
    towards the forecast ("2 of 4 finished periods").
    """
    ai = _teacher_ai(instructor)
    ai['forecast_chart'] = None
    try:
        have = sum(1 for p in finished_periods()
                   if Evaluation.objects.filter(teacher=instructor, period=p).count() >= conf.MIN_RESPONSES)
    except Exception:
        have = 0
    need = conf.MIN_PERIODS
    ai['progress'] = {'have': have, 'need': need, 'percent': min(100, round(have / need * 100))}
    if ai.get('error'):
        return ai
    fc = ai['forecast']
    if fc.get('state') == 'ok':
        c = fc['chart']
        ai['forecast_chart'] = progress_chart(c['labels'][:-1], [v for v in c['actual'][:-1]],
                                              forecast=(c['labels'][-1], c['predicted'][-1]))
    return ai


def teacher_results_ai(instructor, period):
    """AI sections for ONE finished period on the Evaluation Results page."""
    try:
        release = latest_release()
        if release is None:
            return {'error': False, 'released': False,
                    'feedback': {'state': 'not_released'}, 'suggestions': {'state': 'not_released'}}
        cutoff = release.finished_at
        suggestions = _suggestion_block(instructor, period, cutoff)
        if suggestions['state'] == 'not_generated' and not _is_latest(instructor, period):
            suggestions = {'state': 'older_period'}      # suggestions are made for the newest period only
        return {
            'error': False, 'released': True,
            'feedback': _not_included(_feedback_block(instructor, period, cutoff), release, instructor, period),
            'suggestions': _not_included(suggestions, release, instructor, period),
        }
    except Exception:
        log.exception("Could not read AI results")
        return {'error': True}


# ─────────────────────────────────────────────
# ADMIN: aggregate view (no student data, no comment text)
# ─────────────────────────────────────────────
def _cells(teacher_ids, periods):
    """REAL average scores: {(teacher_id, period_id): {...}} (cells with too few answers are left out)."""
    fields = [f for f, _ in CATEGORIES]
    rows = (Evaluation.objects.filter(teacher_id__in=teacher_ids, period__in=periods)
            .values('teacher_id', 'period_id').annotate(n=Count('id'), **{f: Avg(f) for f in fields}))
    cells = {}
    for r in rows:
        if r['n'] < conf.MIN_RESPONSES or any(r[f] is None for f in fields):
            continue
        cats = {f: float(r[f]) for f in fields}
        cells[(r['teacher_id'], r['period_id'])] = {'n': r['n'], **cats, 'overall': sum(cats.values()) / len(fields)}
    return cells


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def admin_analytics():
    """AI Overview: the big picture. Saved results and real averages only."""
    from users.models import User

    teachers = list(User.objects.filter(role='instructor', is_active=True)
                    .select_related('department').order_by('last_name', 'first_name'))
    periods = finished_periods()
    cells = _cells([t.pk for t in teachers], periods)
    used = [p for p in periods if any(k[1] == p.pk for k in cells)]

    # ---- department line chart (REAL overall average per period)
    dept_of = {t.pk: (t.department.name if t.department_id else 'No department') for t in teachers}
    names = sorted(set(dept_of.values()))
    series = []
    for name in names:
        values = [_mean([c['overall'] for (tid, pid), c in cells.items() if pid == p.pk and dept_of[tid] == name])
                  for p in used]
        if any(v is not None for v in values):          # a department with no usable data is left out
            series.append({'name': name, 'values': values})
    trend_chart = charts.line_chart([charts.short_period(p) for p in used], series)

    # ---- forecasts
    grouped = {}
    for row in ForecastResult.objects.filter(status='ok', instructor__in=teachers).select_related('based_on_period'):
        grouped.setdefault(row.instructor_id, []).append(row)
    forecasts = {k: _newest_by_period(v) for k, v in grouped.items()}

    dept_rows = {}
    for t in teachers:
        d = dept_rows.setdefault(dept_of[t.pk], {'name': dept_of[t.pk], 'teachers': 0, 'forecasts': 0,
                                                 'sum_pred': 0.0, 'sum_last': 0.0})
        d['teachers'] += 1
        fc = forecasts.get(t.pk)
        if not fc:
            continue
        history = fc.history or []
        d['forecasts'] += 1
        d['sum_pred'] += fc.predicted_overall
        d['sum_last'] += history[-1]['overall'] if history else 0
    for d in dept_rows.values():
        n = d['forecasts']
        d['avg_pred'] = round(d['sum_pred'] / n, 2) if n else None
        d['avg_last'] = round(d['sum_last'] / n, 2) if n else None
        d['change'] = round(d['avg_pred'] - d['avg_last'], 2) if n else None

    # ---- comments and recommendations (saved results)
    newest = {}
    for r in FeedbackSummary.objects.filter(hidden=False, instructor__in=teachers).select_related('period'):
        cur = newest.get(r.instructor_id)
        if cur is None or sort_key(r.period) > sort_key(cur.period):
            newest[r.instructor_id] = r
    pos = sum(r.positive for r in newest.values())
    neu = sum(r.neutral for r in newest.values())
    neg = sum(r.negative for r in newest.values())
    total = pos + neu + neg
    needs = (Suggestion.objects.filter(instructor__in=teachers, source='rule', priority='high')
             .values('instructor_id').distinct().count())

    # ---- alerts
    active = Alert.objects.filter(instructor__in=teachers)
    new_alerts = active.filter(status='new')
    top_alerts = list(new_alerts.select_related('instructor', 'instructor__department', 'period')
                      .order_by('severity', '-created_at')[:6])       # 'high' sorts before 'medium'

    done = latest_release()
    return {
        'trend_chart': trend_chart,
        'departments': sorted(dept_rows.values(), key=lambda d: d['name']),
        'teacher_count': len(teachers),
        'forecast_count': len(forecasts),
        'insufficient_count': len(teachers) - len(forecasts),
        'latest_run': done.finished_at if done else None,
        'min_periods': conf.MIN_PERIODS,
        'min_responses': conf.MIN_RESPONSES,
        'alerts': {'new': new_alerts.count(), 'high': new_alerts.filter(severity='high').count(),
                   'top': [{'alert': a, 'name': f"{a.instructor.last_name}, {a.instructor.first_name}",
                            'department': a.instructor.department.name if a.instructor.department_id else 'No department',
                            'period_label': period_label(a.period)} for a in top_alerts]},
        'feedback': {'instructors': len(newest), 'positive_pct': round(pos / total * 100) if total else 0,
                     'negative_pct': round(neg / total * 100) if total else 0, 'donut': _donut(pos, neu, neg),
                     'positive': pos, 'neutral': neu, 'negative': neg, 'comments': total},
        'needs_attention': needs,
    }


# ─────────────────────────────────────────────
# ADMIN DASHBOARD: department trends + forecast (bar chart and line chart)
# ─────────────────────────────────────────────
def department_outlook(upto=None):
    """
    For the admin dashboard. Per department:
      * REAL average score in each finished period (solid line)
      * the AI forecast for the NEXT period (dashed line + a bar next to the last real score)
    Only forecasts made from the newest finished period are used, so an old forecast is never shown
    as if it were new. A department without forecasts simply has no dashed line.
    """
    from . import charts as _charts
    from .services import forecasting
    from users.models import User

    from .services.data import periods_up_to

    teachers = list(User.objects.filter(role='instructor', is_active=True).select_related('department'))
    periods = periods_up_to(upto) if upto is not None else finished_periods()
    cells = _cells([t.pk for t in teachers], periods)
    used = [p for p in periods if any(k[1] == p.pk for k in cells)]
    empty = {'line_chart': None, 'bars': [], 'target_label': '', 'has_forecast': False,
             'periods_shown': len(used), 'min_periods': conf.MIN_PERIODS}
    if not used:
        return empty

    last = used[-1]
    target_label = forecasting.next_period_label(last)
    dept_of = {t.pk: (t.department.name if t.department_id else 'No department') for t in teachers}

    grouped = {}
    for row in ForecastResult.objects.filter(status='ok', instructor__in=teachers).select_related('based_on_period'):
        grouped.setdefault(row.instructor_id, []).append(row)
    forecasts = {}
    for tid, rows in grouped.items():
        newest = _newest_by_period(rows)
        if newest and newest.based_on_period_id == last.pk:
            forecasts[tid] = newest

    nxt = forecasting.next_period_info(last)
    years = str(nxt['school_year']).split('-')
    year_short = f"{years[0][-2:]}-{years[1][-2:]}" if len(years) == 2 else str(nxt['school_year'])
    term_names = {'prelims': 'Prelims', 'midterm': 'Midterm', 'prefinals': 'Pre-Finals', 'finals': 'Finals'}
    forecast_short = f"{term_names.get(nxt['term'], nxt['term'])} · {nxt['semester']} Sem {year_short} (forecast)"
    labels = [_charts.short_period(p) for p in used] + [forecast_short]
    series, bars = [], []
    names = sorted(set(dept_of.values()))
    colour = 0
    for name in names:
        real = [_mean([c['overall'] for (tid, pid), c in cells.items() if pid == p.pk and dept_of[tid] == name])
                for p in used]
        mine = [(tid, f) for tid, f in forecasts.items() if dept_of[tid] == name]
        if not any(v is not None for v in real) and not mine:
            continue
        color = _charts.PALETTE[colour % len(_charts.PALETTE)]
        colour += 1
        series.append({'name': name, 'values': real + [None], 'color': color})

        if mine:
            pred = sum(f.predicted_overall for _, f in mine) / len(mine)
            last_real = _mean([(f.history or [{}])[-1].get('overall') for _, f in mine])
            base = real[-1] if real[-1] is not None else last_real
            dashed = [None] * len(used) + [pred]
            if base is not None:
                dashed[len(used) - 1] = base
            series.append({'name': f"{name} (forecast)", 'values': dashed, 'color': color, 'dashed': True})
            bars.append({'name': name, 'forecasts': len(mine),
                         'teachers': sum(1 for v in dept_of.values() if v == name),
                         'avg_last': round(last_real, 2) if last_real is not None else None,
                         'avg_pred': round(pred, 2),
                         'change': round(pred - last_real, 2) if last_real is not None else None,
                         'last_w': round((last_real or 0) / 5 * 100, 1), 'pred_w': round(pred / 5 * 100, 1)})

    return {'line_chart': _charts.line_chart(labels, series), 'bars': bars, 'target_label': target_label,
            'has_forecast': bool(bars), 'periods_shown': len(used), 'min_periods': conf.MIN_PERIODS,
            'last_label': _charts.short_period(last)}


# ─────────────────────────────────────────────
# ADMIN: AI Overview (school level, one chosen period)
# ─────────────────────────────────────────────
ANALYSIS_STATES = {
    'not_generated': 'Not Yet Generated', 'processing': 'Processing', 'completed': 'Completed', 'failed': 'Failed',
}


def scope_period(filters):
    """The period a page should show: the chosen one, else the newest finished period with answers
    inside the chosen school year / semester. None when there is nothing to show."""
    if filters.period:
        return filters.period
    with_data = set(Evaluation.objects.values_list('period_id', flat=True).distinct())
    options = [p for p in finished_periods() if p.pk in with_data and filters.matches(p)]
    return options[-1] if options else None


def analysis_state(period):
    """Not Yet Generated / Processing / Completed / Failed for ONE period, plus the latest time."""
    from .services import analysis

    analysis.mark_stale_runs()
    runs = list(AnalysisRun.objects.filter(period=period).order_by('-version'))
    if not runs:
        return {'key': 'not_generated', 'text': ANALYSIS_STATES['not_generated'], 'run': None, 'at': None,
                'stale': False, 'message': 'No analysis has been made for this period yet.'}
    running = next((r for r in runs if r.is_running), None)
    released = next((r for r in runs if r.is_released), None)
    if running:
        key, shown = 'processing', running
    elif released and runs[0].is_released:
        key, shown = 'completed', released
    elif released:                                    # a newer run failed, an older one still stands
        key, shown = 'failed', runs[0]
    else:
        key, shown = 'failed', runs[0]
    stale = bool(released and released.data_fingerprint != analysis.data_fingerprint(period))
    return {
        'key': key, 'text': ANALYSIS_STATES[key], 'run': shown,
        'at': (released.finished_at if released else None) if key != 'processing' else shown.started_at,
        'version': released.version if released else shown.version,
        'with_errors': bool(released and released.status == AnalysisRun.STATUS_COMPLETED_ERRORS),
        'older_available': bool(released and key == 'failed'),
        'stale': stale, 'message': shown.message,
    }


def _school_scores(periods):
    """{period_id: {'n', 'overall', categories...}} for ALL instructors together (response-weighted)."""
    fields = [f for f, _ in CATEGORIES]
    rows = (Evaluation.objects.filter(period__in=periods, teacher__role='instructor')
            .values('period_id').annotate(n=Count('id'), **{f: Avg(f) for f in fields}))
    out = {}
    for r in rows:
        if r['n'] < conf.MIN_RESPONSES or any(r[f] is None for f in fields):
            continue
        cats = {f: float(r[f]) for f in fields}
        out[r['period_id']] = {'n': r['n'], **cats, 'overall': sum(cats.values()) / len(fields)}
    return out


def _bar_rows(labels_values, scale=5.0):
    return [{'label': label, 'value': round(v, 2) if v is not None else None,
             'width': round((v or 0) / scale * 100, 1)} for label, v in labels_values]


def overview_page(filters):
    """School-level AI Overview for ONE period. Reads saved results and real averages only."""
    from users import dashboard_data
    from users.models import User
    from .services import forecasting
    from .services.data import periods_up_to

    period = scope_period(filters)
    base = {'period': None, 'min_periods': conf.MIN_PERIODS, 'min_responses': conf.MIN_RESPONSES}
    if period is None:
        return base

    teachers = list(User.objects.filter(role='instructor', is_active=True).select_related('department'))
    school = dashboard_data.school_block(period, 'closed')
    periods = periods_up_to(period)
    scores = _school_scores(periods)
    used = [p for p in periods if p.pk in scores]
    state = analysis_state(period)

    # ---- forecast of the NEXT period: from SAVED instructor forecasts made from this period
    saved = {}
    for row in ForecastResult.objects.filter(instructor__in=teachers, based_on_period=period):
        saved.setdefault(row.instructor_id, []).append(row)
    newest = {tid: _newest_by_period(rows) for tid, rows in saved.items()}
    ok = [f for f in newest.values() if f.status == 'ok' and f.predicted_overall is not None]
    with_answers = set(Evaluation.objects.filter(period=period).values_list('teacher_id', flat=True))
    counts = {'ok': len(ok), 'unreliable': 0, 'insufficient': 0, 'none': 0}
    for t in teachers:
        if t.pk not in with_answers:
            continue
        f = newest.get(t.pk)
        if f is None:
            counts['none'] += 1
        elif f.status == 'unreliable':
            counts['unreliable'] += 1
        elif f.status != 'ok':
            counts['insufficient'] += 1
    forecast = {'available': False, 'target_label': forecasting.next_period_label(period), **counts,
                'instructors': len(with_answers & {t.pk for t in teachers})}
    if ok:
        predicted = sum(f.predicted_overall for f in ok) / len(ok)
        lasts = [(f.history or [{}])[-1].get('overall') for f in ok]
        last = _mean(lasts)
        forecast.update(
            available=True, average=round(predicted, 2),
            last_same=round(last, 2) if last is not None else None,
            change=round(predicted - last, 2) if last is not None else None,
            direction=('steady' if last is None or abs(predicted - last) < conf.STEADY_BAND
                       else ('up' if predicted > last else 'down')))

    # ---- school trend line (real, solid) + forecast (dashed)
    labels = [charts.short_period(p) for p in used]
    real = [round(scores[p.pk]['overall'], 2) for p in used]
    lines = [{'name': 'School average (real)', 'values': real, 'color': '#1b2f6b'}]
    if forecast['available'] and used and used[-1].pk == period.pk:
        nxt = forecasting.next_period_info(period)
        years = str(nxt['school_year']).split('-')
        ys = f"{years[0][-2:]}-{years[1][-2:]}" if len(years) == 2 else str(nxt['school_year'])
        names = {'prelims': 'Prelims', 'midterm': 'Midterm', 'prefinals': 'Pre-Finals', 'finals': 'Finals'}
        labels = labels + [f"{names.get(nxt['term'], nxt['term'])} · {nxt['semester']} Sem {ys} (forecast)"]
        lines[0]['values'] = real + [None]
        lines.append({'name': 'AI forecast', 'dashed': True, 'color': '#c9a227',
                      'values': [None] * (len(real) - 1) + [real[-1], forecast['average']]})
    school_chart = charts.line_chart(labels, lines) if used else None

    # ---- category bars (this period and the one before)
    this = scores.get(period.pk)
    prev_period = used[-2] if len(used) >= 2 and used[-1].pk == period.pk else None
    prev = scores.get(prev_period.pk) if prev_period else None
    category_bars = []
    if this:
        for f, label in CATEGORIES:
            category_bars.append({'label': label, 'now': round(this[f], 2), 'now_w': round(this[f] / 5 * 100, 1),
                                  'before': round(prev[f], 2) if prev else None,
                                  'before_w': round(prev[f] / 5 * 100, 1) if prev else 0,
                                  'change': round(this[f] - prev[f], 2) if prev else None})

    # ---- department bars (real score of this period)
    dept_of = {t.pk: (t.department.name if t.department_id else 'No department') for t in teachers}
    cells = _cells(list(dept_of), [period])
    dept_bars = []
    for name in sorted(set(dept_of.values())):
        vals = [c['overall'] for (tid, pid), c in cells.items() if dept_of[tid] == name]
        if vals:
            dept_bars.append({'label': name, 'value': round(sum(vals) / len(vals), 2), 'instructors': len(vals),
                              'width': round(sum(vals) / len(vals) / 5 * 100, 1)})

    # ---- feedback numbers (counts only) and the saved school text
    rows = list(FeedbackSummary.objects.filter(period=period, hidden=False))
    pos, neu, neg = (sum(r.positive for r in rows), sum(r.neutral for r in rows), sum(r.negative for r in rows))
    total_c = pos + neu + neg
    feedback = {'instructors': len(rows), 'comments': sum(r.n_analyzed for r in rows),
                'received': sum(r.n_comments for r in rows),
                'positive': pos, 'neutral': neu, 'negative': neg, 'donut': _donut(pos, neu, neg),
                'positive_pct': round(pos / total_c * 100) if total_c else 0,
                'negative_pct': round(neg / total_c * 100) if total_c else 0}
    insight = SchoolInsight.objects.filter(period=period).first()

    # ---- alerts that need attention in this period
    open_alerts = list(Alert.objects.filter(period=period).exclude(status='resolved')
                       .select_related('instructor', 'instructor__department').order_by('severity', '-created_at'))
    by_pk = {t.pk: t for t in teachers}
    top = []
    for a in open_alerts[:5]:
        t = by_pk.get(a.instructor_id)
        top.append({'alert': a, 'name': f"{t.last_name}, {t.first_name}" if t else '', 'department': dept_of.get(a.instructor_id, '')})

    outlook = department_outlook(upto=period)
    return {
        **base, 'period': period, 'label': period_label(period), 'state': state,
        'school': school, 'responses': this['n'] if this else 0, 'category_bars': category_bars,
        'school_chart': school_chart, 'periods_used': len(used), 'enough_history': len(used) >= conf.MIN_PERIODS,
        'forecast': forecast, 'dept_bars': dept_bars, 'outlook': outlook,
        'feedback': feedback, 'insight': insight,
        'alerts': {'open': len(open_alerts), 'high': sum(1 for a in open_alerts if a.severity == 'high'), 'top': top},
        'previous_label': period_label(prev_period) if prev_period else '',
    }


# ─────────────────────────────────────────────
# ADMIN: Performance Forecasting page
# ─────────────────────────────────────────────
FORECAST_PAGE_SIZE = 15
FORECAST_CHART_MAX = 25      # the bar chart stays readable


def _in_term(row, term):
    """True when the forecast was made from a period of the chosen school year + semester."""
    period = row.based_on_period
    return period is not None and f"{period.school_year}|{period.semester}" == term


def _forecast_row(teacher, fc):
    """One table row. `fc` is the newest saved ForecastResult of this instructor (any status) or None."""
    department = teacher.department.name if teacher.department_id else 'No department'
    base = {'teacher': teacher, 'name': f"{teacher.last_name}, {teacher.first_name}", 'department': department}

    if fc is None:
        return {**base, 'state': 'none', 'message': '', 'periods_used': 0, 'generated_at': None,
                'quality': {}, 'last_actual': None, 'based_on_label': ''}

    history = fc.history or []
    last = history[-1]['overall'] if history else None
    common = {
        **base, 'fc': fc, 'quality': fc.data_quality or {}, 'last_actual': last,
        'generated_at': fc.generated_at, 'periods_used': fc.periods_used,
        'based_on_label': period_label(fc.based_on_period) if fc.based_on_period_id else '',
        'model_version': fc.model_version or 'older version',
        'responses': history[-1]['responses'] if history else 0,
    }
    if fc.status != 'ok':
        return {**common, 'state': fc.status, 'message': fc.message}

    change = None if last is None else round(fc.predicted_overall - last, 2)
    return {
        **common, 'state': 'ok', 'predicted': fc.predicted_overall,
        'low': fc.range_low, 'high': fc.range_high, 'change': change,
        'direction': 'steady' if change is None or abs(change) < conf.STEADY_BAND
                     else ('up' if change > 0 else 'down'),
        'method': MODEL_TEXT.get(fc.model_name, fc.model_name),
        'mae': fc.validation_mae, 'baseline': fc.baseline_mae,
        'beats_baseline': (fc.validation_mae is not None and fc.baseline_mae is not None
                           and fc.validation_mae < fc.baseline_mae),
        'reliability': fc.reliability, 'target_label': fc.target_label,
    }


def _detail_chart(fc):
    """Real vs predicted for ONE instructor. Predicted values for past periods come from the
    walk-forward test (each one made using only the periods before it)."""
    history = fc.history or []
    tested = {b['label']: b['predicted'] for b in (fc.backtest or [])}
    labels = [h['label'] for h in history] + [fc.target_label or 'Next period']
    return {
        'labels': labels,
        'actual': [h['overall'] for h in history] + [None],
        'tested': [tested.get(h['label']) for h in history] + [None],
        'next': [None] * len(history) + [fc.predicted_overall],
        'low': fc.range_low, 'high': fc.range_high,
    }


def forecast_page(filters, page=1):
    """Everything the Performance Forecasting page needs. Reads SAVED results only."""
    teachers = list(filtered_instructors(filters))

    saved = list(
        ForecastResult.objects.filter(instructor__in=teachers)
        .select_related('based_on_period').order_by('generated_at')
    )
    grouped = {}
    for row in saved:
        if filters.period and row.based_on_period_id != filters.period.pk:
            continue
        if not filters.period and row.based_on_period is not None and not filters.matches(row.based_on_period):
            continue
        grouped.setdefault(row.instructor_id, []).append(row)

    # one row per instructor: the saved result based on the newest period (any status)
    rows = [_forecast_row(t, _newest_by_period(grouped.get(t.pk, []))) for t in teachers]
    ok_rows = [r for r in rows if r['state'] == 'ok']

    chart_rows = ok_rows[:FORECAST_CHART_MAX]
    bar = {
        'labels': [r['name'] for r in chart_rows],
        'actual': [r['last_actual'] for r in chart_rows],
        'predicted': [r['predicted'] for r in chart_rows],
    }

    detail = None
    if filters.instructor:
        mine = next((r for r in rows if r['teacher'].pk == filters.instructor.pk), None)
        if mine and mine['state'] == 'ok':
            fc = mine['fc']
            categories = [{**fc.predicted_categories[f], 'key': f}
                          for f, _ in CATEGORIES if f in fc.predicted_categories]
            detail = {'row': mine, 'chart': _detail_chart(fc), 'categories': categories,
                      'has_backtest': bool(fc.backtest)}
        else:
            detail = {'row': mine, 'chart': None}
            if mine and mine['state'] == 'baseline':       # show the one real score we do have
                detail['history'] = (mine['fc'].history or [])

    pager = Paginator(rows, FORECAST_PAGE_SIZE).get_page(page)
    changes = [r['change'] for r in ok_rows if r['change'] is not None]
    return {
        'pager': pager,
        'bar': bar, 'bar_cut': len(ok_rows) > FORECAST_CHART_MAX, 'bar_max': FORECAST_CHART_MAX,
        'detail': detail,
        'stats': {
            'instructors': len(rows),
            'with_forecast': len(ok_rows),
            'not_enough': sum(1 for r in rows if r['state'] in ('insufficient', 'baseline', 'none')),
            'unreliable': sum(1 for r in rows if r['state'] == 'unreliable'),
            'errors': sum(1 for r in rows if r['state'] == 'error'),
            'avg_change': round(sum(changes) / len(changes), 2) if changes else None,
            'latest_run': max((r['generated_at'] for r in rows if r['generated_at']), default=None),
            'versions': sorted({r['model_version'] for r in ok_rows}),
        },
        'min_periods': conf.MIN_PERIODS, 'min_responses': conf.MIN_RESPONSES,
    }


# ─────────────────────────────────────────────
# ADMIN: the "Generate AI Analysis" panel (status, progress, versions)
# ─────────────────────────────────────────────
def run_summary(run):
    """A small, safe dictionary about one run (no student data). Also used for the progress JSON."""
    return {
        'id': run.pk, 'version': run.version, 'period_id': run.period_id,
        'period_label': period_label(run.period),
        'status': run.status, 'status_text': run.get_status_display(),
        'running': run.is_running, 'released': run.is_released,
        'trigger_text': run.get_trigger_display(),
        'started_at': run.started_at, 'finished_at': run.finished_at,
        'total': run.total_instructors, 'processed': run.processed, 'failed': run.failed,
        'skipped': run.skipped, 'percent': run.percent, 'message': run.message,
        'engine_version': run.engine_version, 'regenerate': run.regenerate,
    }


def generate_panel(selected_period_id=None):
    """Everything the Generate panel needs. Reads only; it never starts a run."""
    from django.utils import timezone
    from .services import analysis

    analysis.mark_stale_runs()
    runs = list(AnalysisRun.objects.select_related('period').order_by('-started_at')[:30])
    newest_run = {}                                   # newest run of each period
    for r in runs:
        if r.period_id not in newest_run or r.version > newest_run[r.period_id].version:
            newest_run[r.period_id] = r
    released = {}
    for r in runs:
        if r.is_released and (r.period_id not in released or r.version > released[r.period_id].version):
            released[r.period_id] = r

    with_data = set(Evaluation.objects.values_list('period_id', flat=True).distinct())
    options = []
    for period in reversed(finished_periods()):       # newest first
        if period.pk not in with_data:
            continue
        done = released.get(period.pk)
        if done is None:
            note = 'not analysed yet'
        else:
            stale = done.data_fingerprint != analysis.data_fingerprint(period)
            note = (f"analysed v{done.version}, {timezone.localtime(done.finished_at):%b %d, %Y}"
                    + (" · new data since" if stale else " · up to date"))
        options.append({'pk': period.pk, 'label': period_label(period), 'note': note, 'done': done is not None})

    chosen = None
    wanted = None
    try:
        wanted = int(selected_period_id) if selected_period_id not in (None, '') else None
    except (TypeError, ValueError):
        wanted = None
    if wanted and any(o['pk'] == wanted for o in options):
        chosen = wanted
    elif options:
        chosen = next((o['pk'] for o in options if not o['done']), options[0]['pk'])

    running = next((r for r in runs if r.is_running), None)
    latest = latest_release()
    from .services import ollama
    return {
        'ollama': ollama.status(),
        'options': options, 'chosen': chosen,
        'running': run_summary(running) if running else None,
        'latest': run_summary(latest) if latest else None,
        'last_failed': run_summary(runs[0]) if runs and runs[0].status == 'failed' else None,
        'recent': [run_summary(r) for r in runs[:5]],
        'has_periods': bool(options),
    }


# ─────────────────────────────────────────────
# ADMIN: Student Feedback Analysis page (instructor level; never single comments)
# ─────────────────────────────────────────────
FEEDBACK_PAGE_SIZE = 10
TONES = {'negative': 'Mostly negative', 'positive': 'Mostly positive'}
SOURCES = {'ollama': 'AI-written (Ollama)', 'rule': 'Rule-based'}


def _choice(value, allowed):
    return value if value in allowed else ''


def _newest_summary_per_instructor(rows, period):
    grouped = {}
    for r in rows:
        if period and r.period_id != period.pk:
            continue
        grouped.setdefault(r.instructor_id, []).append(r)
    return {k: max(v, key=lambda r: (sort_key(r.period), r.generated_at)) for k, v in grouped.items()}


ANON_COMMENT_LIMIT = 25


def anonymous_comments(instructor, period):
    """
    The real student comments of one instructor in one period, made anonymous:
      * no student name, no date, no order of arrival (sorted by a hash, so it looks shuffled but stays stable)
      * e-mails, links, phone numbers and @handles removed
      * empty / junk comments left out, and at most ANON_COMMENT_LIMIT shown
    The caller must only use this when the comment summary of that instructor is NOT hidden
    (enough students and comments), so one student cannot be recognised.
    """
    import hashlib

    from .services import ollama, sentiment

    texts = []
    for text in Evaluation.objects.filter(teacher=instructor, period=period).exclude(comment='').values_list('comment', flat=True):
        cleaned = sentiment.clean_comment(text)
        if cleaned:
            texts.append(ollama.scrub(cleaned))
    texts = [t for t in texts if t]
    texts.sort(key=lambda t: hashlib.sha256(t.encode('utf-8')).hexdigest())
    return texts[:ANON_COMMENT_LIMIT]


def feedback_page(filters, tone='', source='', page=1):
    tone, source = _choice(tone, TONES), _choice(source, SOURCES)
    teachers = list(filtered_instructors(filters))
    by_id = {t.pk: t for t in teachers}
    rows = list(FeedbackSummary.objects.filter(instructor__in=teachers).select_related('period'))
    if not filters.period:
        rows = [r for r in rows if filters.matches(r.period)]
    newest = _newest_summary_per_instructor(rows, filters.period)
    written_total = sum(r.n_comments for r in newest.values())          # every student who wrote something
    hidden_reasons = sorted({r.hidden_reason for r in newest.values() if r.hidden and r.hidden_reason})

    visible, hidden = [], 0
    for instructor_id, r in newest.items():
        if r.hidden:
            hidden += 1
            continue
        total = r.positive + r.neutral + r.negative
        pos, neg = (r.positive / total, r.negative / total) if total else (0, 0)
        if tone == 'negative' and neg < 0.4:
            continue
        if tone == 'positive' and pos < 0.6:
            continue
        if source and r.summary_source != source:
            continue
        t = by_id[instructor_id]
        subs = r.subthemes or []
        visible.append({
            'teacher': t, 'name': f"{t.last_name}, {t.first_name}",
            'department': t.department.name if t.department_id else 'No department',
            'period_label': period_label(r.period), 'row': r, 'ai': r.summary_source == 'ollama',
            'pos_pct': round(pos * 100), 'neg_pct': round(neg * 100),
            'neu_pct': max(0, 100 - round(pos * 100) - round(neg * 100)) if total else 0,
            'problems': [s for s in subs if s.get('tone') == 'problem'][:3],
            'praise': [s for s in subs if s.get('tone') == 'praise'][:2],
        })
    visible.sort(key=lambda v: (-v['neg_pct'], v['name']))

    pos = sum(v['row'].positive for v in visible)
    neu = sum(v['row'].neutral for v in visible)
    neg = sum(v['row'].negative for v in visible)
    total = pos + neu + neg

    depts = {}
    topic_counts = {}
    for v in visible:
        d = depts.setdefault(v['department'], {'name': v['department'], 'positive': 0, 'neutral': 0, 'negative': 0})
        d['positive'] += v['row'].positive
        d['neutral'] += v['row'].neutral
        d['negative'] += v['row'].negative
        for sub in v['row'].subthemes or []:
            c = topic_counts.setdefault(sub['key'], {**sub, 'mentions': 0, 'instructors': 0})
            c['mentions'] += sub['mentions']
            c['instructors'] += 1
    dept_bars = []
    for d in sorted(depts.values(), key=lambda x: x['name']):
        n = d['positive'] + d['neutral'] + d['negative']
        dept_bars.append({**d, 'total': n,
                          'pos': round(d['positive'] / n * 100) if n else 0,
                          'neg': round(d['negative'] / n * 100) if n else 0,
                          'neu': max(0, 100 - round(d['positive'] / n * 100) - round(d['negative'] / n * 100)) if n else 0})
    topics = sorted(topic_counts.values(), key=lambda c: (-c['mentions'], c['key']))

    pager = Paginator(visible, FEEDBACK_PAGE_SIZE).get_page(page)

    # actual student comments (anonymous), only for ONE chosen instructor whose summary is not hidden
    raw = None
    if filters.instructor:
        mine = newest.get(filters.instructor.pk)
        if mine is None:
            raw = {'state': 'none', 'comments': []}
        elif mine.hidden:
            raw = {'state': 'hidden', 'reason': mine.hidden_reason, 'comments': []}
        else:
            raw = {'state': 'ok', 'period_label': period_label(mine.period),
                   'comments': anonymous_comments(filters.instructor, mine.period)}

    # AI suggestions for the same filters (top items; the full list is on its own page)
    sugg = recommendations_page(filters)
    top_suggestions = [c for c in sugg['pager'].object_list if c['priority'] in ('high', 'medium')][:8]

    return {
        'pager': pager, 'tone': tone, 'source': source, 'tones': TONES, 'sources': SOURCES,
        'raw': raw, 'all_items': visible, 'suggestions': top_suggestions, 'suggestion_total': sugg['stats']['total'],
        'hidden_reasons': hidden_reasons,
        'dept_bars': dept_bars,
        'problems': _topic_rows([t for t in topics if t['tone'] == 'problem'][:8]),
        'praise': _topic_rows([t for t in topics if t['tone'] == 'praise'][:8]),
        'stats': {
            'instructors': len(visible), 'hidden': hidden, 'without': len(teachers) - len(newest),
            'written': written_total,
            'comments': sum(v['row'].n_analyzed for v in visible),
            'positive': pos, 'neutral': neu, 'negative': neg,
            'positive_pct': round(pos / total * 100) if total else 0,
            'negative_pct': round(neg / total * 100) if total else 0,
            'ai_written': sum(1 for v in visible if v['ai']),
            'donut': _donut(pos, neu, neg),
        },
        'min_responses': conf.MIN_RESPONSES, 'min_comments': conf.MIN_COMMENTS,
    }


# ─────────────────────────────────────────────
# ADMIN: Recommendations page
# ─────────────────────────────────────────────
RECOMMENDATION_PAGE_SIZE = 12
PRIORITIES = {'high': 'Needs attention', 'medium': 'Can improve', 'low': 'Small idea', 'strength': 'Strength'}


def recommendations_page(filters, priority='', category='', source='', page=1):
    priority = _choice(priority, PRIORITIES)
    category = _choice(category, dict(CATEGORIES))
    source = _choice(source, SOURCES)
    teachers = list(filtered_instructors(filters))
    by_id = {t.pk: t for t in teachers}

    rows = list(Suggestion.objects.filter(instructor__in=teachers).select_related('period'))
    if not filters.period:
        rows = [r for r in rows if filters.matches(r.period)]
    if filters.period:
        rows = [r for r in rows if r.period_id == filters.period.pk]

    # one period per instructor: the newest that has suggestions
    newest_period = {}
    for r in rows:
        cur = newest_period.get(r.instructor_id)
        if cur is None or sort_key(r.period) > sort_key(cur):
            newest_period[r.instructor_id] = r.period
    mine = {}
    for r in rows:
        if r.period_id == newest_period[r.instructor_id].pk:
            mine.setdefault(r.instructor_id, []).append(r)

    cards, all_cards = [], []
    for instructor_id, group in mine.items():
        t = by_id[instructor_id]
        for item in _suggestion_items(group):
            card = {**item, 'teacher': t, 'name': f"{t.last_name}, {t.first_name}",
                    'department': t.department.name if t.department_id else 'No department',
                    'period_label': period_label(newest_period[instructor_id])}
            all_cards.append(card)
            if priority and card['priority'] != priority:
                continue
            if category and card['key'] != category:
                continue
            if source == 'ollama' and not card['ai']:
                continue
            if source == 'rule' and card['ai']:
                continue
            cards.append(card)
    order = {'high': 0, 'medium': 1, 'low': 2, 'strength': 3}
    cards.sort(key=lambda c: (order.get(c['priority'], 9), c['score'] if c['score'] is not None else 5, c['name']))

    by_category = []
    for key, label in CATEGORIES:
        mine_c = [c for c in all_cards if c['key'] == key]
        by_category.append({
            'key': key, 'label': label,
            'high': sum(1 for c in mine_c if c['priority'] == 'high'),
            'medium': sum(1 for c in mine_c if c['priority'] == 'medium'),
            'low': sum(1 for c in mine_c if c['priority'] == 'low'),
            'strength': sum(1 for c in mine_c if c['priority'] == 'strength'),
        })
    for c in by_category:
        c['total'] = c['high'] + c['medium'] + c['low'] + c['strength']
    biggest = max((c['total'] for c in by_category), default=1) or 1
    for c in by_category:
        for k in ('high', 'medium', 'low', 'strength'):
            c[k + '_w'] = round(c[k] / biggest * 100)

    pager = Paginator(cards, RECOMMENDATION_PAGE_SIZE).get_page(page)
    return {
        'pager': pager, 'all_cards': cards, 'priority': priority, 'category': category, 'source': source,
        'priorities': PRIORITIES, 'categories': CATEGORIES, 'sources': SOURCES,
        'by_category': by_category,
        'stats': {
            'instructors': len(mine), 'total': len(all_cards),
            'high': sum(1 for c in all_cards if c['priority'] == 'high'),
            'medium': sum(1 for c in all_cards if c['priority'] == 'medium'),
            'low': sum(1 for c in all_cards if c['priority'] == 'low'),
            'strength': sum(1 for c in all_cards if c['priority'] == 'strength'),
            'ai_written': sum(1 for c in all_cards if c['ai']),
            'shown': len(cards),
        },
    }


# ─────────────────────────────────────────────
# ADMIN: Performance Trends page (REAL scores only, nothing predicted)
# ─────────────────────────────────────────────
METRICS = [('overall', 'Overall score')] + list(CATEGORIES)


TREND_PAGE_SIZE = 10


def _forecast_short_label(last):
    """Short x-axis label of the next period, e.g. 'Prelims · 2nd Sem 26-27 (forecast)'."""
    from .services import forecasting
    nxt = forecasting.next_period_info(last)
    years = str(nxt['school_year']).split('-')
    year_short = f"{years[0][-2:]}-{years[1][-2:]}" if len(years) == 2 else str(nxt['school_year'])
    names = {'prelims': 'Prelims', 'midterm': 'Midterm', 'prefinals': 'Pre-Finals', 'finals': 'Finals'}
    return f"{names.get(nxt['term'], nxt['term'])} · {nxt['semester']} Sem {year_short} (forecast)"


def _trend_word(change):
    if change is None:
        return 'none'
    if change > conf.STEADY_BAND:
        return 'improving'
    if change < -conf.STEADY_BAND:
        return 'declining'
    return 'stable'


def trends_page(filters, metric='overall', page=1):
    """
    Admin "AI Performance Trends". REAL scores and AI forecasts are always kept apart:
      * `rows`            one row per instructor: real scores of the shown period + the saved AI forecast
      * `history_chart`   solid line = real scores, dashed line = AI forecast (only when one is saved)
      * `radar`           the five categories (this scope vs the school)
      * `dept_compare`    instructors inside each department      * `dept_bars`  department vs department
    A forecast is shown only when it was made from the shown period. Nothing is estimated here.
    """
    from .services.data import periods_up_to

    metric = metric if metric in dict(METRICS) else 'overall'
    teachers = list(filtered_instructors(filters))
    by_id = {t.pk: t for t in teachers}
    dept_of = {t.pk: (t.department.name if t.department_id else 'No department') for t in teachers}

    if filters.period:                                    # history up to the chosen period
        periods = periods_up_to(filters.period)
    else:
        periods = [p for p in finished_periods() if filters.matches(p)]
    cells = _cells(list(by_id), periods)
    used = [p for p in periods if any(k[1] == p.pk for k in cells)]
    labels = [charts.short_period(p) for p in used]
    shown = filters.period if (filters.period and filters.period in used) else (used[-1] if used else None)

    # ----- chart 1 and 2 (kept from before): categories over time, departments over time
    cat_series = [{'name': label, 'values': [
        _mean([c[f] for (tid, pid), c in cells.items() if pid == p.pk]) for p in used]} for f, label in CATEGORIES]
    cat_series.insert(0, {'name': 'Overall', 'values': [
        _mean([c['overall'] for (tid, pid), c in cells.items() if pid == p.pk]) for p in used]})
    dept_series = []
    for name in sorted(set(dept_of.values())):
        values = [_mean([c[metric] for (tid, pid), c in cells.items() if pid == p.pk and dept_of[tid] == name])
                  for p in used]
        if any(v is not None for v in values):
            dept_series.append({'name': name, 'values': values})

    # ----- who improved / declined (last two usable periods of every instructor)
    moves = []
    for t in teachers:
        mine = sorted([(sort_key(p), p, cells[(t.pk, p.pk)]) for p in used if (t.pk, p.pk) in cells],
                      key=lambda x: x[0])
        if len(mine) < 2:
            continue
        before, after = mine[-2][2][metric], mine[-1][2][metric]
        moves.append({'teacher': t, 'name': f"{t.last_name}, {t.first_name}", 'department': dept_of[t.pk],
                      'before': round(before, 2), 'after': round(after, 2), 'change': round(after - before, 2),
                      'from_label': charts.short_period(mine[-2][1]), 'to_label': charts.short_period(mine[-1][1])})
    band = conf.STEADY_BAND
    improved = sorted([m for m in moves if m['change'] > band], key=lambda m: -m['change'])
    declined = sorted([m for m in moves if m['change'] < -band], key=lambda m: m['change'])

    last_avg = cat_series[0]['values'][-1] if used else None
    prev_avg = cat_series[0]['values'][-2] if len(used) >= 2 else None

    # ----- saved AI forecasts that were made from the SHOWN period
    forecasts = {}
    if shown is not None:
        grouped = {}
        for fc in ForecastResult.objects.filter(instructor__in=teachers).select_related('based_on_period'):
            grouped.setdefault(fc.instructor_id, []).append(fc)
        for tid, rows in grouped.items():
            newest = _newest_by_period(rows)
            if newest is not None and newest.based_on_period_id == shown.pk:
                forecasts[tid] = newest

    # ----- the table: one row per instructor for the shown period
    rows, skipped = [], 0
    for t in teachers:
        cell = cells.get((t.pk, shown.pk)) if shown else None
        mine = sorted([(sort_key(p), p) for p in used if (t.pk, p.pk) in cells], key=lambda x: x[0])
        before = None
        for _, p in mine:
            if shown and sort_key(p) < sort_key(shown):
                before = cells[(t.pk, p.pk)]
        if cell is None:
            if shown and Evaluation.objects.filter(teacher=t, period=shown).exists():
                skipped += 1
            continue
        cats = [(f, label, cell[f]) for f, label in CATEGORIES]
        top = max(cats, key=lambda c: c[2])
        low = min(cats, key=lambda c: c[2])
        change = None if before is None else round(cell['overall'] - before['overall'], 2)
        fc = forecasts.get(t.pk)
        frow = _forecast_row(t, fc) if fc is not None else None
        rows.append({
            'teacher': t, 'name': f"{t.last_name}, {t.first_name}", 'department': dept_of[t.pk],
            'responses': cell['n'], 'overall': round(cell['overall'], 2),
            'categories': [{'key': f, 'label': label, 'score': round(v, 2)} for f, label, v in cats],
            'strongest': top[1] if top[2] > low[2] else '', 'weakest': low[1] if top[2] > low[2] else '',
            'change': change, 'trend': _trend_word(change),
            'periods_with_scores': len(mine),
            'forecast': frow,
            'forecast_text': (f"{frow['predicted']:.2f}" if frow and frow['state'] == 'ok'
                              else ('Not reliable enough' if frow and frow['state'] == 'unreliable'
                                    else ('Not enough history' if len(mine) < conf.MIN_PERIODS
                                          else 'Not analysed yet'))),
        })
    rows.sort(key=lambda r: (-r['overall'], r['name']))
    pager = Paginator(rows, TREND_PAGE_SIZE).get_page(page)

    # ----- real vs forecast line chart (one instructor, or the average of everybody shown)
    history_chart = None
    if used:
        f_label = _forecast_short_label(used[-1]) if shown == used[-1] else None
        chart_labels = labels + ([f_label] if f_label else [])
        real = [_mean([c['overall'] for (tid, pid), c in cells.items() if pid == p.pk]) for p in used]
        series = [{'name': 'Real average score' if not filters.instructor else 'Real score',
                   'values': real + ([None] if f_label else []), 'color': charts.PALETTE[0]}]
        if f_label and forecasts:
            pred = _mean([f.predicted_overall for f in forecasts.values()])
            dashed = [None] * len(used) + [pred]
            if real[-1] is not None:
                dashed[len(used) - 1] = real[-1]
            series.append({'name': 'AI forecast', 'values': dashed, 'color': charts.PALETTE[1], 'dashed': True})
        history_chart = charts.line_chart(chart_labels, series)

    # ----- radar: this scope vs the whole school, in the shown period
    radar = None
    if shown is not None and rows:
        mine = [cells[(r['teacher'].pk, shown.pk)] for r in rows]
        school_cells = _cells([u.pk for u in User.objects.filter(role='instructor', is_active=True)], [shown])
        axes = [label for _, label in CATEGORIES]
        scope_name = (f"{filters.instructor.last_name}, {filters.instructor.first_name}" if filters.instructor
                      else (filters.department.name if filters.department else 'All shown instructors'))
        series = [{'name': scope_name, 'values': [_mean([c[f] for c in mine]) for f, _ in CATEGORIES],
                   'color': charts.PALETTE[0]}]
        if school_cells and (filters.instructor or filters.department):
            series.append({'name': 'Whole school', 'values': [_mean([c[f] for c in school_cells.values()])
                                                            for f, _ in CATEGORIES],
                           'color': charts.PALETTE[1], 'dashed': True})
        radar = charts.radar_chart(axes, series)

    # ----- comparisons inside each department, and department vs department
    dept_compare, dept_bars = [], []
    for name in sorted({r['department'] for r in rows}):
        inside = [r for r in rows if r['department'] == name]
        values = [(r, next(c['score'] for c in r['categories'] if c['key'] == metric) if metric != 'overall'
                   else r['overall']) for r in inside]
        values.sort(key=lambda x: (-x[1], x[0]['name']))
        avg = sum(v for _, v in values) / len(values)
        dept_compare.append({'name': name, 'average': round(avg, 2), 'rows': [
            {'name': r['name'], 'teacher': r['teacher'], 'value': round(v, 2), 'width': round(v / 5 * 100, 1),
             'responses': r['responses']} for r, v in values]})
        dept_bars.append({'label': name, 'value': round(avg, 2), 'width': round(avg / 5 * 100, 1),
                          'instructors': len(values)})
    dept_bars.sort(key=lambda d: -d['value'])

    # ----- detailed forecast of ONE instructor (the same detail as the forecasting page)
    detail = None
    if filters.instructor:
        detail = forecast_page(filters)['detail']

    total_evals = (Evaluation.objects.filter(teacher__in=teachers, period__in=used).count() if used else 0)
    shown_evals = Evaluation.objects.filter(teacher__in=teachers, period=shown).count() if shown else 0
    with_fc = sum(1 for r in rows if r['forecast'] and r['forecast']['state'] == 'ok')
    metric_series = next((x for x in cat_series if x['name'] == dict(METRICS)[metric]), cat_series[0])

    return {
        'metric': metric, 'metrics': METRICS, 'metric_label': dict(METRICS)[metric],
        'category_labels': [label for _, label in CATEGORIES],
        'shown': shown, 'shown_label': period_label(shown) if shown else '',
        'cat_chart': charts.line_chart(labels, cat_series),
        'dept_chart': charts.line_chart(labels, dept_series),
        'history_chart': history_chart, 'radar': radar,
        'pager': pager, 'all_rows': rows, 'row_count': len(rows), 'skipped': skipped,
        'dept_compare': dept_compare, 'dept_bars': dept_bars, 'detail': detail,
        'stats': {
            'periods': len(used), 'instructors': len({k[0] for k in cells}),
            'latest': None if last_avg is None else round(last_avg, 2),
            'change': None if (last_avg is None or prev_avg is None) else round(last_avg - prev_avg, 2),
            'improved': len(improved), 'declined': len(declined),
            'total_evaluations': total_evals, 'shown_evaluations': shown_evals,
            'with_forecast': with_fc, 'metric_avg': None,
        },
        'improved': improved[:5], 'declined': declined[:5],
        'forecast_ready': bool(forecasts), 'min_periods': conf.MIN_PERIODS, 'min_responses': conf.MIN_RESPONSES,
    }


# ─────────────────────────────────────────────
# ADMIN: Performance Alerts page
# ─────────────────────────────────────────────
ALERT_PAGE_SIZE = 10
ALERT_STATUS = {'new': 'New', 'reviewed': 'Reviewed', 'resolved': 'Resolved'}
ALERT_SEVERITY = {'high': 'High', 'medium': 'Medium', 'low': 'Low'}
SEVERITY_ORDER = {'high': 0, 'medium': 1, 'low': 2}
STATUS_ORDER = {'new': 0, 'reviewed': 1, 'resolved': 2}
ALERT_KINDS = dict(Alert.KIND_CHOICES)


def alert_support(alert):
    """The real numbers behind an alert as plain (label, value) pairs for the page."""
    e = alert.evidence or {}
    rows = []
    names = {'overall': 'Overall score', 'limit': 'Limit', 'responses': 'Evaluations', 'from': 'Score before',
             'to': 'Score now', 'change': 'Change', 'periods': 'Periods in a row', 'predicted': 'AI forecast',
             'low': 'Forecast range from', 'high': 'Forecast range to', 'last': 'Last real score',
             'negative': 'Negative comments', 'analysed': 'Comments analysed', 'share': 'Negative share (%)'}
    for key, label in names.items():
        if key in e and e[key] is not None:
            rows.append((label, f"{e[key]:+.2f}" if key == 'change' else e[key]))
    if e.get('scores'):
        rows.append(('Scores', ' → '.join(f"{v:.2f}" for v in e['scores'])))
    for c in e.get('categories', []):
        rows.append((c['label'], c['score']))
    return rows


def alerts_page(filters, status='', severity='', kind='', page=1):
    status, severity, kind = _choice(status, ALERT_STATUS), _choice(severity, ALERT_SEVERITY), _choice(kind, ALERT_KINDS)
    teachers = list(filtered_instructors(filters))
    by_id = {t.pk: t for t in teachers}
    base = Alert.objects.filter(instructor__in=teachers).select_related('period', 'reviewed_by')
    if filters.period:
        base = base.filter(period=filters.period)
    else:
        if filters.school_year:
            base = base.filter(period__school_year=filters.school_year)
        if filters.semester:
            base = base.filter(period__semester=filters.semester)
    if severity:
        base = base.filter(severity=severity)
    if kind:
        base = base.filter(kind=kind)

    counts = {k: base.filter(status=k).count() for k in ALERT_STATUS}
    shown = base.filter(status=status) if status else base
    shown = sorted(shown.order_by('-created_at'),                          # new first, then high first
                   key=lambda a: (STATUS_ORDER.get(a.status, 9), SEVERITY_ORDER.get(a.severity, 9)))
    pager = Paginator(shown, ALERT_PAGE_SIZE).get_page(page)
    for a in shown:
        t = by_id[a.instructor_id]
        a.name = f"{t.last_name}, {t.first_name}"
        a.department = t.department.name if t.department_id else 'No department'
        a.period_text = period_label(a.period)
        a.support = alert_support(a)

    # instructors that need attention, grouped by department (open alerts only)
    open_alerts = list(base.exclude(status='resolved'))
    shown_period = filters.period or (max((a.period for a in open_alerts), key=sort_key) if open_alerts else None)
    groups = {}
    for a in open_alerts:
        t = by_id[a.instructor_id]
        dept = t.department.name if t.department_id else 'No department'
        row = groups.setdefault(dept, {}).setdefault(t.pk, {
            'teacher': t, 'name': f"{t.last_name}, {t.first_name}", 'alerts': 0, 'severity': 'low', 'kinds': []})
        row['alerts'] += 1
        row['kinds'].append(a.get_kind_display())
        if SEVERITY_ORDER[a.severity] < SEVERITY_ORDER[row['severity']]:
            row['severity'] = a.severity
    attention = []
    for dept in sorted(groups):
        rows = list(groups[dept].values())
        for row in rows:
            cell = _cells([row['teacher'].pk], [shown_period]).get((row['teacher'].pk, shown_period.pk)) if shown_period else None
            row['average'] = round(cell['overall'], 2) if cell else None
            row['evaluations'] = cell['n'] if cell else 0
        rows.sort(key=lambda r: (SEVERITY_ORDER[r['severity']], -r['alerts'], r['name']))
        attention.append({'department': dept, 'rows': rows})

    by_period = {}
    for a in base:
        label = period_label(a.period)
        item = by_period.setdefault(a.period_id, {'label': label, 'key': sort_key(a.period), 'count': 0, 'open': 0})
        item['count'] += 1
        item['open'] += a.status != 'resolved'
    history = sorted(by_period.values(), key=lambda x: x['key'], reverse=True)
    top = max((h['count'] for h in history), default=1) or 1
    for h in history:
        h['width'] = round(h['count'] / top * 100)

    from evaluation.models import Evaluation as _Ev
    few = 0
    if shown_period is not None:
        per = _Ev.objects.filter(period=shown_period, teacher__in=teachers).values('teacher_id').annotate(n=Count('id'))
        few = sum(1 for r in per if r['n'] < conf.MIN_RESPONSES)

    by_kind = []
    for key, label in Alert.KIND_CHOICES:
        n = base.filter(kind=key).exclude(status='resolved').count()
        by_kind.append({'key': key, 'label': label, 'count': n})
    biggest = max((k['count'] for k in by_kind), default=1) or 1
    for k in by_kind:
        k['width'] = round(k['count'] / biggest * 100)

    return {
        'pager': pager, 'all_alerts': shown, 'status': status, 'severity': severity, 'kind': kind,
        'statuses': ALERT_STATUS, 'severities': ALERT_SEVERITY, 'kinds': Alert.KIND_CHOICES,
        'counts': counts, 'total': sum(counts.values()), 'by_kind': by_kind,
        'high_new': base.filter(status='new', severity='high').count(),
        'severity_counts': {k: base.exclude(status='resolved').filter(severity=k).count() for k in ALERT_SEVERITY},
        'attention': attention, 'attention_period': period_label(shown_period) if shown_period else '',
        'history': history, 'too_few': few, 'min_responses': conf.MIN_RESPONSES,
        'settings': AlertSettings.get(),
    }


# ─────────────────────────────────────────────
# ADMIN: one instructor, everything together
# ─────────────────────────────────────────────
def instructor_detail(teacher):
    series, quality = analyse_history(teacher)
    labels = [charts.short_period(x['period']) for x in series]
    lines = [{'name': 'Overall', 'values': [x['overall'] for x in series]}]
    lines += [{'name': label, 'values': [x['categories'][f] for x in series]} for f, label in CATEGORIES]
    latest = series[-1] if series else None

    forecast = _forecast_block(teacher)
    forecast_chart = None
    if forecast.get('state') == 'ok':
        chart = forecast['chart']
        forecast_chart = charts.line_chart(
            [charts.short_period(x['period']) for x in series] + [chart['labels'][-1]],
            [{'name': 'Real score', 'values': [x['overall'] for x in series] + [None]},
             {'name': 'AI prediction', 'values': [None] * (len(series) - 1) +
              [series[-1]['overall'], forecast['row'].predicted_overall], 'dashed': True, 'color': '#c9a227'}])

    period = latest['period'] if latest else None
    return {
        'teacher': teacher,
        'department': teacher.department.name if teacher.department_id else 'No department',
        'series': series, 'quality': quality, 'latest': latest,
        'trend_chart': charts.line_chart(labels, lines),
        'forecast': forecast, 'forecast_chart': forecast_chart,
        'label': period_label(period) if period else '',
        'feedback': _feedback_block(teacher, period) if period else {'state': 'no_period'},
        'suggestions': _suggestion_block(teacher, period) if period else {'state': 'no_period'},
        'alerts': list(Alert.objects.filter(instructor=teacher).select_related('period')
                       .order_by('status', 'severity', '-created_at')),
        'min_responses': conf.MIN_RESPONSES,
    }