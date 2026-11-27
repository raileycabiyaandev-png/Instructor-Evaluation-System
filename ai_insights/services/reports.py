# ai_insights/services/reports.py
#
# AI REPORTS: PDF and Excel files made from SAVED results only (no model is run here).
#
# A report is a plain dictionary:
#   {'title', 'subtitle', 'landscape': bool, 'sections': [{'heading', 'text', 'headers', 'rows'}]}
# The same dictionary is turned into a PDF (reportlab) or an Excel file (openpyxl), so both files
# always say the same thing.
#
# Privacy: single comments and student names never appear. Instructor reports show only summaries.
# The instructor's "My report" is built from results that are already official (released) and
# never contains the administrator's alerts or notes.

import io
import re
from xml.sax.saxutils import escape

from django.utils import timezone

from evaluation.models import Evaluation

from .. import selectors
from ..models import Alert, FeedbackSummary, ForecastResult
from .data import CATEGORIES, analyse_history, period_label, sort_key


class ReportLibraryMissing(Exception):
    """reportlab or openpyxl is not installed."""


def _stamp():
    release = selectors.latest_release()
    now = timezone.localtime().strftime('%b %d, %Y %H:%M')
    if release is None:
        return f"Made {now}. No official AI analysis has been generated yet."
    return (f"Made {now}. AI analysis: version {release.version} of {period_label(release.period)}, "
            f"finished {timezone.localtime(release.finished_at).strftime('%b %d, %Y %H:%M')}.")


def _num(v, digits=2):
    return '' if v is None else f"{v:.{digits}f}"


# ─────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────
INDICATOR_TEXT = {'improving': 'Improving', 'declining': 'Declining', 'stable': 'Stable', None: 'No earlier period to compare'}


def instructor_report(teacher, cutoff=None, admin=True, period=None):
    """
    One instructor's report. `period` is the finished period the report is about (default: the newest one
    with answers). Real scores, the AI forecast and the AI texts are in separate sections.
    cutoff: only AI results saved before this time are used (the official analysis); None = all (admin).
    Student names and single comments never appear.
    """
    name = f"{teacher.last_name}, {teacher.first_name}"
    series_all, quality = analyse_history(teacher)
    from .data import finished_periods, period_snapshot, sort_key

    rated = [p for p in finished_periods() if Evaluation.objects.filter(teacher=teacher, period=p).exists()]
    if period is None and rated:
        period = rated[-1]
    snap = period_snapshot(teacher, period) if period else None
    series = [x for x in series_all if period is None or sort_key(x['period']) <= sort_key(period)]
    is_latest = bool(rated) and period is not None and rated[-1].pk == period.pk
    sections = []

    # ---- 1. Summary (the numbers a decision starts from)
    if snap and snap['has_data']:
        prev = snap['previous']
        change = snap['change']
        text = (f"{snap['label']}: {snap['responses']} of {snap['expected']} students answered"
                if snap['expected'] else f"{snap['label']}: {snap['responses']} students answered")
        text += f", and the overall score is {snap['overall']:.2f} out of 5."
        if prev:
            text += (f" Compared with {prev['label']} ({prev['overall']:.2f}) the change is {change:+.2f} "
                     f"({INDICATOR_TEXT[snap['indicator']].lower()}).")
        if snap['low_sample']:
            text += " Fewer than 3 students answered, so read the numbers with care."
        attention = ', '.join(f"{r['label']} ({r['score']:.2f})" for r in snap['attention']) or 'None'
        sections.append({
            'heading': 'Summary', 'text': text, 'headers': ['Item', 'Result'],
            'rows': [['Period', snap['label']],
                     ['Students who answered', f"{snap['responses']} of {snap['expected']} ({snap['percent']}%)"
                      if snap['expected'] else str(snap['responses'])],
                     ['Overall score', _num(snap['overall'])],
                     ['Previous period', f"{prev['label']}: {_num(prev['overall'])}" if prev else 'None'],
                     ['Change', f"{change:+.2f}" if change is not None else ''],
                     ['Indicator', INDICATOR_TEXT[snap['indicator']]],
                     ['Strongest area', f"{snap['strongest']['label']} ({_num(snap['strongest']['score'])})"],
                     ['Weakest area', f"{snap['weakest']['label']} ({_num(snap['weakest']['score'])})"],
                     ['Needs attention', attention]],
        })
        bar = {'type': 'bar', 'title': 'Score in each category (out of 5)',
               'labels': [r['label'] for r in snap['categories']], 'values': [r['score'] for r in snap['categories']]}
        sections.append({
            'heading': 'Category scores', 'text': '', 'charts': [bar],
            'headers': ['Category', 'Score', 'Previous period', 'Change', 'Level'],
            'rows': [[r['label'], _num(r['score']), _num(r['previous']) if r['previous'] is not None else '',
                      f"{r['diff']:+.2f}" if r['diff'] is not None else '',
                      {'high': 'Excellent', 'mid': 'Good', 'low': 'Needs improvement'}[r['level']]]
                     for r in snap['categories']],
        })
    else:
        sections.append({'heading': 'Summary', 'headers': [], 'rows': [],
                         'text': 'No finished evaluation period with answers was found, so there is nothing to report yet.'})

    # ---- 2. History (real scores) with the line chart; the forecast is drawn dashed when it exists
    forecast = selectors._forecast_block(teacher, cutoff)
    chart_labels = [selectors._short_label(x['label']) for x in series]
    chart_series = [{'name': 'Real score', 'values': [x['overall'] for x in series], 'dashed': False}]
    if is_latest and forecast.get('state') == 'ok' and series:
        row = forecast['row']
        chart_labels.append(selectors._short_label(row.target_label or 'Next period'))
        chart_series[0]['values'].append(None)
        chart_series.append({'name': 'AI forecast', 'dashed': True,
                             'values': [None] * (len(series) - 1) + [series[-1]['overall'], row.predicted_overall]})
    history = {'type': 'line', 'title': 'Real score over time', 'labels': chart_labels, 'series': chart_series}
    sections.append({
        'heading': 'Real scores by period',
        'text': '' if series else 'No finished evaluation has enough responses yet.',
        'charts': [history] if series else [],
        'headers': ['Period', 'Responses', 'Overall'] + [label for _, label in CATEGORIES],
        'rows': [[x['label'], x['responses'], _num(x['overall'])] + [_num(x['categories'][f]) for f, _ in CATEGORIES]
                 for x in reversed(series)],
    })

    # ---- 3. AI forecast
    if is_latest and forecast.get('state') == 'ok':
        row = forecast['row']
        sections.append({
            'heading': 'AI forecast (a prediction, not a fact)',
            'text': f"The forecast learned from {row.periods_used} finished periods. "
                    f"{'It beat' if forecast.get('beats_baseline') else 'It did not beat'} the simple guess "
                    f"\"same as last period\" in past tests.",
            'headers': ['Next period', 'Predicted', 'Range', 'Last real score', 'Change', 'Reliability'],
            'rows': [[row.target_label or 'Next period', _num(row.predicted_overall),
                      f"{_num(row.range_low)} to {_num(row.range_high)}", _num(forecast['last_actual']),
                      _num(forecast['change']), row.reliability or '']],
        })
    else:
        if not is_latest and period is not None:
            message = 'A forecast is shown only in the report of your newest finished period.'
        else:
            message = forecast.get('message') or (
                f"No forecast yet. It needs at least 4 finished periods with enough responses "
                f"(you have {quality['periods_used']}).")
        sections.append({'heading': 'AI forecast', 'text': message, 'headers': [], 'rows': []})

    # ---- 4. Student feedback summary
    feedback = selectors._feedback_block(teacher, period, cutoff) if period else {'state': 'no_period'}
    if feedback.get('state') == 'ok':
        row = feedback['row']
        source = f"AI summary ({row.llm_model})" if feedback['ai'] else 'Rule-based summary'
        total = row.positive + row.neutral + row.negative
        sections.append({
            'heading': f'Student feedback summary · {period_label(period)}',
            'text': f"{source}: {row.summary_text}",
            'headers': ['Sentiment', 'Comments', 'Share'],
            'rows': [[b['label'], b['count'], f"{b['percent']}%"] for b in feedback['bars']] +
                    [['Total analysed', total, '']],
        })
        points = ([['Liked', p] for p in feedback['positive_points']] +
                  [['To improve', p] for p in feedback['negative_points']])
        if points:
            sections.append({'heading': 'What students liked and want improved', 'text': '',
                             'headers': ['Type', 'Point'], 'rows': points})
        topics = ([['To improve', t['label'], t['mentions']] for t in feedback['problems']] +
                  [['Liked', t['label'], t['mentions']] for t in feedback['praise']])
        if topics:
            sections.append({'heading': 'Topics students mention', 'text': '',
                             'headers': ['Type', 'Topic', 'Comments'], 'rows': topics})
    else:
        sections.append({'heading': 'Student feedback summary', 'headers': [], 'rows': [],
                         'text': 'No comment summary is available (not generated yet, or too few comments to protect privacy).'})

    # ---- 5. Recommendations
    suggestions = selectors._suggestion_block(teacher, period, cutoff) if period else {'state': 'no_period'}
    if suggestions.get('state') == 'ok':
        sections.append({
            'heading': 'Recommendations', 'text': '',
            'headers': ['Category', 'Priority', 'Score', 'Written by', 'Recommendation'],
            'rows': [[i['label'], i['priority_text'], _num(i['score']), 'AI' if i['ai'] else 'Rules', i['text']]
                     for i in suggestions['items']],
        })
    else:
        sections.append({'heading': 'Recommendations', 'headers': [], 'rows': [],
                         'text': 'No recommendations were saved for this period.'})

    if admin:
        alerts = Alert.objects.filter(instructor=teacher).select_related('period').order_by('status', 'severity')
        sections.append({
            'heading': 'Alerts', 'text': '' if alerts else 'No alert.',
            'headers': ['Period', 'Alert', 'Severity', 'Status', 'Details'] if alerts else [],
            'rows': [[period_label(a.period), a.get_kind_display(), a.get_severity_display(),
                      a.get_status_display(), a.message] for a in alerts],
        })

    # ---- 6. Data notes (what the reader must know before using the numbers)
    notes = [f"Scores are the average of five categories from student ratings (1 to 5).",
             "Student names and single comments are never included. Comments are summarised only when "
             "enough students answered.",
             "The forecast is a prediction made from past scores. It is not a real score."]
    if quality['skipped']:
        notes.append("Left out of the forecast (too few responses): " +
                     '; '.join(f"{x['label']} ({x['responses']})" for x in quality['skipped']) + '.')
    sections.append({'heading': 'Data notes', 'text': '', 'headers': ['Note'], 'rows': [[n] for n in notes]})

    department = teacher.department.name if teacher.department_id else 'No department'
    return {'title': f"Evaluation report · {name}", 'subtitle': f"{department}. {_stamp()}",
            'landscape': True, 'sections': sections,
            'meta': [['Instructor', name], ['Department', department],
                     ['Period', period_label(period) if period else 'None'],
                     ['Report generated', timezone.localtime().strftime('%b %d, %Y %H:%M')]]}


LEVELS = {'school': 'School report', 'department': 'Department report', 'instructor': 'Instructor report'}
TREND_WORD = {'improving': 'Improving', 'declining': 'Declining', 'stable': 'Stable'}


def _line_spec(chart, title):
    """Turn an SVG-chart dict (charts.line_chart) into a plain spec the PDF maker can draw."""
    if not chart:
        return None
    dashed = {l['name']: l['dashed'] for l in chart['lines']}
    return {'type': 'line', 'title': title, 'labels': chart['labels'],
            'series': [{'name': r['name'], 'values': r['values'], 'dashed': dashed.get(r['name'], False)}
                       for r in chart['table']]}


def analytics_report(filters, level='school'):
    """
    The full AI evaluation report for the chosen filters.
      level 'school'      every instructor of the school
      level 'department'  one department (or one block per department when none is chosen)
      level 'instructor'  one instructor
    Real scores and AI forecasts are in separate columns. A forecast that cannot be made says why.
    No student comment and no student name is ever copied in.
    """
    from dataclasses import replace

    from ..models import SchoolInsight

    if level not in LEVELS:
        level = 'school'
    f = filters
    if level == 'school':
        f = replace(f, department=None, instructor=None)
    elif level == 'department':
        f = replace(f, instructor=None)
    period = selectors.scope_period(f)
    scope = {'school': 'Whole school', 'department': f.department.name if f.department else 'All departments',
             'instructor': f"{f.instructor.last_name}, {f.instructor.first_name}" if f.instructor else ''}[level]
    title = f"AI evaluation report · {scope}"
    if period is None:
        return {'title': title, 'subtitle': _stamp(), 'landscape': True,
                'meta': [['Report level', LEVELS[level]], ['Report generated', timezone.localtime().strftime('%b %d, %Y %H:%M')]],
                'sections': [{'heading': 'Executive Summary', 'headers': [], 'rows': [],
                              'text': 'There is no finished evaluation period with answers for these filters, so there is nothing to report.'}]}

    fp = replace(f, period=period)
    trends = selectors.trends_page(fp)
    feedback = selectors.feedback_page(fp)
    alerts_data = selectors.alerts_page(fp)
    recs = selectors.recommendations_page(fp)
    rows = trends['all_rows']
    stats = trends['stats']
    state = selectors.analysis_state(period)
    insight = SchoolInsight.objects.filter(period=period).first() if level == 'school' else None
    min_p, min_r = trends['min_periods'], trends['min_responses']

    meta = [
        ['Report level', LEVELS[level]], ['Scope', scope],
        ['Academic year', period.school_year], ['Semester', period.get_semester_display()],
        ['Evaluation period', period.get_term_display()],
        ['Selected filters', _filter_text(filters)],
        ['Evaluations received (this period)', stats['shown_evaluations']],
        ['Report generated', timezone.localtime().strftime('%b %d, %Y %H:%M')],
        ['AI analysis status', state['text'] + (f" ({timezone.localtime(state['at']).strftime('%b %d, %Y %H:%M')})" if state.get('at') else '')],
    ]
    sections = []

    # 1. Executive summary
    forecasts_ok = stats['with_forecast']
    lines = [
        f"In {trends['shown_label']}, {stats['shown_evaluations']} evaluations were received for {len(rows)} instructor(s) with enough answers.",
        (f"The average real score is {stats['latest']:.2f} out of 5"
         + (f", {stats['change']:+.2f} compared with the period before." if stats['change'] is not None else ".")
         if stats['latest'] is not None else "No average score is available."),
        (f"{forecasts_ok} instructor(s) have an AI forecast for the next period (a prediction, not a fact)."
         if forecasts_ok else f"No AI forecast is available. A forecast needs at least {min_p} finished periods with {min_r} or more evaluations each."),
        f"{alerts_data['counts']['new']} new and {alerts_data['counts']['reviewed']} reviewed performance alert(s).",
    ]
    sections.append({'heading': 'Executive Summary', 'text': ' '.join(lines), 'headers': ['Item', 'Value'], 'rows': [
        ['Average real score', _num(stats['latest'])], ['Change since the period before', _num(stats['change'])],
        ['Evaluations received (this period)', stats['shown_evaluations']],
        ['Evaluations in all periods shown', stats['total_evaluations']],
        ['Instructors with enough evaluations', len(rows)], ['Instructors with an AI forecast', forecasts_ok],
        ['Instructors improved / declined', f"{stats['improved']} / {stats['declined']}"],
    ] + ([['AI summary (' + ('Ollama' if insight.summary_source == 'ollama' else 'rules') + ')', insight.summary_text]] if insight else [])})

    # 2. School / scope performance by period
    hist = trends['history_chart']
    real_row = next((r for r in hist['table'] if not r['name'].startswith('AI')), None) if hist else None
    perf_rows = []
    if hist and real_row:
        perf_rows = [[lab, _num(v)] for lab, v in zip(hist['labels'], real_row['values']) if v is not None]
    sections.append({
        'heading': 'School Performance' if level == 'school' else 'Performance of the scope',
        'text': '' if perf_rows else 'No finished period has enough evaluations.',
        'headers': ['Period', 'Average real score'] if perf_rows else [], 'rows': perf_rows,
        'charts': [c for c in [_line_spec(hist, 'Real score by period (solid) and AI forecast (dashed)')] if c]})

    # 3. Departments
    dept_rows = [[d['label'], d['instructors'], _num(d['value'])] for d in trends['dept_bars']]
    outlook = selectors.department_outlook(upto=period) if level != 'instructor' else None
    forecast_rows = [[b['name'], _num(b['avg_last']), _num(b['avg_pred']), _num(b['change']), f"{b['forecasts']} of {b['teachers']}"]
                     for b in (outlook['bars'] if outlook else [])]
    sections.append({
        'heading': 'Department Performance', 'text': '' if dept_rows else 'No department has an instructor with enough evaluations.',
        'headers': ['Department', 'Instructors', 'Average real score'] if dept_rows else [], 'rows': dept_rows,
        'charts': [{'type': 'bar', 'title': 'Average real score by department', 'labels': [d['label'] for d in trends['dept_bars']],
                    'values': [d['value'] for d in trends['dept_bars']]}] if dept_rows else []})
    if level != 'instructor':
        sections.append({
            'heading': 'Department forecast (AI prediction, shown apart from real scores)',
            'text': '' if forecast_rows else f"No department forecast is available. It needs at least {min_p} finished periods per instructor and an analysis run.",
            'headers': ['Department', 'Last real average', 'AI forecast average', 'Change', 'Instructors with a forecast'] if forecast_rows else [],
            'rows': forecast_rows,
            'charts': [c for c in [_line_spec(outlook['line_chart'], 'Department averages by period (dashed = AI forecast)')] if c] if outlook else []})

    # 4. Instructor performance
    sections.append({
        'heading': 'Instructor Performance',
        'text': '' if rows else 'No instructor has enough evaluations in this period.',
        'headers': ['Instructor', 'Department', 'Evaluations', 'Real average', 'Change', 'Trend'] if rows else [],
        'rows': [[r['name'], r['department'], r['responses'], _num(r['overall']), _num(r['change']),
                  TREND_WORD.get(r['trend'], 'First period')] for r in rows]})

    # 5. Category scores
    cat_labels = trends['category_labels']
    cat_rows = [[r['name']] + [_num(c['score']) for c in r['categories']] for r in rows]
    if rows:
        avg = [sum(r['categories'][i]['score'] for r in rows) / len(rows) for i in range(len(cat_labels))]
        cat_rows.append(['Average of the instructors above'] + [_num(v) for v in avg])
    radar = trends['radar']
    sections.append({
        'heading': 'Evaluation Category Scores', 'text': '' if rows else 'No category scores yet.',
        'headers': ['Instructor'] + cat_labels if rows else [], 'rows': cat_rows,
        'charts': [{'type': 'radar', 'title': 'Average score in the five categories', 'axes': radar['axes'],
                    'series': [{'name': r['name'], 'values': r['values']} for r in radar['table']]}] if radar else []})

    # 6. Historical trends
    cat_chart = trends['cat_chart']
    hist_rows = []
    if cat_chart:
        for i, lab in enumerate(cat_chart['labels']):
            hist_rows.append([lab] + [_num(r['values'][i]) for r in cat_chart['table']])
    sections.append({
        'heading': 'Historical Trends', 'text': '' if hist_rows else 'Not enough finished periods.',
        'headers': ['Period'] + [r['name'] for r in cat_chart['table']] if hist_rows else [], 'rows': hist_rows,
        'charts': [c for c in [_line_spec(cat_chart, 'Real average score of each category by period')] if c]})

    # 7. AI forecasting results (actual and forecast apart)
    fc_rows = []
    for r in rows:
        fc = r['forecast']
        if fc and fc['state'] == 'ok':
            fc_rows.append([r['name'], _num(fc['last_actual']), _num(fc['predicted']), f"{_num(fc['low'])} to {_num(fc['high'])}",
                            _num(fc['change']), fc.get('target_label', ''), fc.get('reliability') or ''])
        else:
            why = (fc or {}).get('message') or r['forecast_text']
            fc_rows.append([r['name'], '', '', '', '', '', why])
    sections.append({
        'heading': 'AI Forecasting Results (a prediction, not a fact)',
        'text': ("Actual (real) scores and AI forecasts are in separate columns. Where there is no forecast, the last column says why."
                 if fc_rows else 'No instructor to forecast.'),
        'headers': ['Instructor', 'Last real score', 'AI forecast', 'Typical range', 'Change', 'Forecast for', 'Reliability / reason'] if fc_rows else [],
        'rows': fc_rows})

    # 8. Student feedback summaries (AI summaries only, never single comments)
    items = feedback['all_items']
    sections.append({
        'heading': 'AI Student Feedback Summaries',
        'text': (f"{feedback['stats']['written']} student comments were written; {feedback['stats']['comments']} were analysed. "
                 "Only summaries are shown. Single comments and student names are never included."
                 if items or feedback['stats']['written'] else
                 'No comment summary is available (not generated yet, or too few comments to protect students).'),
        'headers': ['Instructor', 'Comments analysed', 'Positive', 'Neutral', 'Negative', 'Written by', 'Summary'] if items else [],
        'rows': [[v['name'], v['row'].n_analyzed, v['row'].positive, v['row'].neutral, v['row'].negative,
                  'AI (Ollama)' if v['ai'] else 'Rules', v['row'].summary_text] for v in items]})

    # 9, 10. strengths and weaknesses
    strengths = [['School', x['text'], 'AI' if x['source'] == 'ollama' else 'Rules'] for x in (insight.strengths if insight else [])]
    weaknesses = [['School', x['text'], 'AI' if x['source'] == 'ollama' else 'Rules'] for x in (insight.weaknesses if insight else [])]
    for r in rows:
        if r['strongest']:
            strengths.append([r['name'], f"Highest category: {r['strongest']}", 'Real scores'])
        if r['weakest']:
            weaknesses.append([r['name'], f"Lowest category: {r['weakest']}", 'Real scores'])
    for v in items:
        for t in v['praise']:
            strengths.append([v['name'], f"Students like: {t['label']} ({t['mentions']} comments)", 'Comment themes'])
        for t in v['problems']:
            weaknesses.append([v['name'], f"Students want improved: {t['label']} ({t['mentions']} comments)", 'Comment themes'])
    sections.append({'heading': 'Strengths', 'text': '' if strengths else 'No clear strength found.',
                     'headers': ['Who', 'Strength', 'Based on'] if strengths else [], 'rows': strengths})
    sections.append({'heading': 'Weaknesses', 'text': '' if weaknesses else 'No clear weakness found.',
                     'headers': ['Who', 'Weakness', 'Based on'] if weaknesses else [], 'rows': weaknesses})

    # 11. alerts
    alert_list = alerts_data['all_alerts']
    sections.append({
        'heading': 'Performance Alerts', 'text': '' if alert_list else 'No performance alert for these filters.',
        'headers': ['Instructor', 'Department', 'Period', 'Alert', 'Severity', 'Status', 'Reason'] if alert_list else [],
        'rows': [[a.name, a.department, a.period_text, a.get_kind_display(), a.get_severity_display(),
                  a.get_status_display(), a.message] for a in alert_list]})

    # 12. recommendations
    rec_rows = [['School', x.get('label', ''), x['text'], 'AI' if x['source'] == 'ollama' else 'Rules']
                for x in (insight.suggestions if insight else [])]
    rec_rows += [[c['name'], c['label'], c['text'], 'AI' if c['ai'] else 'Rules']
                 for c in recs['all_cards'] if c['priority'] in ('high', 'medium')]
    sections.append({
        'heading': 'AI-Generated Recommendations', 'text': 'Advice written from real scores and comment themes. It is guidance, not a fact.' if rec_rows else 'No recommendation yet. Generate the AI analysis first.',
        'headers': ['For', 'Area', 'Recommendation', 'Written by'] if rec_rows else [], 'rows': rec_rows})

    # 13. data limitations (so nobody reads more into the numbers than they hold)
    no_fc = [r for r in rows if not (r['forecast'] and r['forecast']['state'] == 'ok')]
    limits = [
        ['Scores', f"Only instructors with at least {min_r} evaluations in the period are scored. {trends['skipped']} instructor(s) were left out for this reason."],
        ['Forecasts', f"A forecast needs at least {min_p} finished periods with {min_r}+ evaluations each. {len(no_fc)} of {len(rows)} instructor(s) have no usable forecast."
                      " A forecast is a prediction, not a fact, and is shown apart from real scores."],
        ['Student comments', f"{feedback['stats']['hidden']} instructor(s) have comment summaries hidden to protect students (too few students or comments)."
                             " Only summaries are used; single comments are never included."],
        ['AI analysis', 'Not generated yet for this period.' if state['key'] == 'not_generated' else
                        ('The last analysis failed.' if state['key'] == 'failed' else
                         ('New evaluations came in after the last analysis.' if state.get('stale') else 'Up to date.'))],
        ['AI text', 'Summaries and suggestions are written by a local AI (Ollama) or by plain rules, from the scores and comment themes above. Each item is labelled.'],
    ]
    sections.append({'heading': 'Data Limitations', 'text': '', 'headers': ['Topic', 'Note'], 'rows': limits})

    return {'title': title, 'subtitle': _stamp(), 'landscape': True, 'meta': meta, 'sections': sections}


def _filter_text(f):
    parts = [f"School year: {f.school_year or 'All'}", f"Semester: {f.semester or 'All'}",
             f"Period: {f.period.get_term_display() if f.period else 'Latest in scope'}",
             f"Department: {f.department.name if f.department else 'All'}",
             f"Instructor: {(f.instructor.last_name + ', ' + f.instructor.first_name) if f.instructor else 'All'}"]
    return ' · '.join(parts)


# ─────────────────────────────────────────────
# File makers
# ─────────────────────────────────────────────
def file_name(report, fmt):
    base = re.sub(r'[^A-Za-z0-9]+', '-', report['title']).strip('-').lower()[:60] or 'report'
    return f"{base}-{timezone.localdate().isoformat()}.{fmt}"


def to_xlsx(report):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise ReportLibraryMissing("Excel export needs the openpyxl package. Run: pip install openpyxl") from exc

    def put(ws, row, col, value, **style):
        cell = ws.cell(row=row, column=col, value=value)
        if isinstance(value, str):
            cell.data_type = 's'                 # text is never read as a formula (blocks "=1+1" tricks)
        for k, v in style.items():
            setattr(cell, k, v)
        return cell

    wb = Workbook()
    ws = wb.active
    ws.title = 'Report'
    put(ws, 1, 1, report['title'], font=Font(bold=True, size=14))
    put(ws, 2, 1, report['subtitle'])
    for i, (label, value) in enumerate(report.get('meta', []), start=4):
        put(ws, i, 1, f"{label}: {value}")
    ws.column_dimensions['A'].width = 110
    used = {'Report'}
    for i, section in enumerate(report['sections'], start=1):
        name = re.sub(r'[\[\]:*?/\\]', ' ', f"{i} {section['heading']}")[:31].strip()
        while name in used:
            name = name[:28] + f"~{i}"
        used.add(name)
        sheet = wb.create_sheet(name)
        put(sheet, 1, 1, section['heading'], font=Font(bold=True, size=12))
        row = 2
        if section.get('text'):
            put(sheet, row, 1, section['text'], alignment=Alignment(wrap_text=True, vertical='top'))
            row += 1
        row += 1
        for c, head in enumerate(section['headers'], start=1):
            put(sheet, row, c, head, font=Font(bold=True, color='FFFFFF'),
                fill=PatternFill('solid', fgColor='1F3B8A'), alignment=Alignment(wrap_text=True, vertical='center'))
        for r_i, values in enumerate(section['rows'], start=row + 1):
            for c, v in enumerate(values, start=1):
                put(sheet, r_i, c, v, alignment=Alignment(wrap_text=True, vertical='top'))
        for c in range(1, max(len(section['headers']), 1) + 1):
            longest = max([len(str(section['headers'][c - 1])) if section['headers'] else 10] +
                          [len(str(v[c - 1])) for v in section['rows'] if len(v) >= c] or [10])
            sheet.column_dimensions[chr(64 + c) if c <= 26 else 'Z'].width = min(60, max(12, longest + 2))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


CHART_COLORS = ['#1f3b8a', '#c9a227', '#2f7d4f', '#b4532a', '#6b4fa3', '#2a8c9a']


def _draw_chart(spec, width):
    """Draw one chart (line, bar or radar) with reportlab.graphics. Returns a Drawing or None."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.charts.spider import SpiderChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    height = 230 if spec['type'] != 'radar' else 250
    d = Drawing(width, height)
    palette = [colors.HexColor(c) for c in CHART_COLORS]

    if spec['type'] == 'line':
        series = [s for s in spec['series'] if any(v is not None for v in s['values'])]
        if not series:
            return None
        values = [v for s in series for v in s['values'] if v is not None]
        import math
        lo, hi = max(1.0, math.floor(min(values) * 2) / 2), min(5.0, math.ceil(max(values) * 2) / 2)
        if hi - lo < 1:
            hi = min(5.0, lo + 1)
            lo = max(1.0, hi - 1)
        chart = HorizontalLineChart()
        chart.x, chart.y, chart.width, chart.height = 40, 75, width - 190, height - 95
        chart.data = [[None if v is None else float(v) for v in s['values']] for s in series]
        chart.categoryAxis.categoryNames = [str(l)[:30] for l in spec['labels']]
        chart.categoryAxis.labels.angle = 30
        chart.categoryAxis.labels.boxAnchor = 'ne'
        chart.categoryAxis.labels.fontSize = 6
        chart.valueAxis.valueMin, chart.valueAxis.valueMax = lo, hi
        chart.valueAxis.valueStep = 0.5
        chart.valueAxis.labels.fontSize = 7
        chart.valueAxis.labelTextFormat = '%.1f'
        for i, s in enumerate(series):
            chart.lines[i].strokeColor = palette[i % len(palette)]
            chart.lines[i].strokeWidth = 1.6
            chart.lines[i].symbol = None
            if s.get('dashed'):
                chart.lines[i].strokeDashArray = [4, 3]
        d.add(chart)
        legend = Legend()
        legend.x, legend.y, legend.fontSize, legend.alignment = width - 140, height - 20, 7, 'right'
        legend.dy = legend.dx = 7
        legend.colorNamePairs = [(palette[i % len(palette)], s['name'][:26] + (' (forecast)' if s.get('dashed') and 'forecast' not in s['name'].lower() else ''))
                                 for i, s in enumerate(series)]
        d.add(legend)
        return d

    if spec['type'] == 'bar':
        if not spec['values']:
            return None
        chart = VerticalBarChart()
        chart.x, chart.y, chart.width, chart.height = 40, 50, width - 60, height - 70
        chart.data = [[float(v) for v in spec['values']]]
        chart.categoryAxis.categoryNames = [str(l)[:24] for l in spec['labels']]
        chart.categoryAxis.labels.fontSize = 7
        chart.valueAxis.valueMin, chart.valueAxis.valueMax, chart.valueAxis.valueStep = 0, 5, 1
        chart.valueAxis.labels.fontSize = 7
        chart.bars[0].fillColor = palette[0]
        chart.barLabelFormat = '%.2f'
        chart.barLabels.fontSize = 7
        chart.barLabels.nudge = 6
        d.add(chart)
        return d

    if spec['type'] == 'radar':
        series = [s for s in spec['series'] if any(v is not None for v in s['values'])]
        if not series:
            return None
        chart = SpiderChart()
        chart.x, chart.y, chart.width, chart.height = width / 2 - 90, 30, 180, 180
        chart.data = [[0 if v is None else float(v) for v in s['values']] for s in series]
        chart.data.append([5.0] * len(spec['axes']))      # invisible outer ring, so the scale is always 0 to 5
        chart.labels = [str(a) for a in spec['axes']]
        chart.strands.strokeWidth = 1.5
        for i in range(len(series)):
            chart.strands[i].strokeColor = palette[i % len(palette)]
        chart.strands[len(series)].strokeColor = colors.HexColor('#d5daea')
        chart.strands[len(series)].strokeWidth = 0.5
        chart.spokes.strokeWidth = 0.4
        d.add(chart)
        legend = Legend()
        legend.x, legend.y, legend.fontSize = width - 150, height - 20, 7
        legend.colorNamePairs = [(palette[i % len(palette)], s['name'][:28]) for i, s in enumerate(series)]
        d.add(legend)
        return d
    return None


def to_pdf(report):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ReportLibraryMissing("PDF export needs the reportlab package. Run: pip install reportlab") from exc

    page = landscape(A4) if report.get('landscape') else A4
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=report['title'])
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Title'], fontSize=17, alignment=0, textColor=colors.HexColor('#1f3b8a'))
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#1f3b8a'), spaceBefore=10)
    body = ParagraphStyle('b', parent=styles['BodyText'], fontSize=9, leading=12)
    cell = ParagraphStyle('c', parent=body, fontSize=8, leading=10)
    head = ParagraphStyle('hc', parent=cell, textColor=colors.white, fontName='Helvetica-Bold')

    def p(text, style):
        return Paragraph(escape(str(text)), style)          # escape: text can never act as markup

    story = [p(report['title'], h1), p(report['subtitle'], body), Spacer(1, 3 * mm)]
    width = page[0] - 28 * mm
    if report.get('meta'):
        meta = Table([[p(k, head), p(v, cell)] for k, v in report['meta']], colWidths=[width * .25, width * .75])
        meta.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#1f3b8a')),
                                  ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#c9d0e6')),
                                  ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story += [meta, Spacer(1, 4 * mm)]
    for section in report['sections']:
        top = [p(section['heading'], h2)]
        if section.get('text'):
            top.append(p(section['text'], body))
        first = True
        for spec in section.get('charts', []):
            drawing = _draw_chart(spec, width)
            if drawing is None:
                continue
            block = [Spacer(1, 2 * mm), p(spec['title'], body), drawing]
            if first:                                   # heading, text and first chart stay on one page
                story.append(KeepTogether(top + block))
                top, first = [], False
            else:
                story += [KeepTogether(block), Spacer(1, 2 * mm)]
        story += top                                    # sections without a chart
        if section['headers'] and section['rows']:
            data = [[p(h, head) for h in section['headers']]] + [[p(v, cell) for v in row] for row in section['rows']]
            n = len(section['headers'])
            widths = None
            # wide text columns get more room
            longest = [max([len(str(section['headers'][c]))] + [len(str(r[c])) for r in section['rows'] if len(r) > c])
                       for c in range(n)]
            weights = [min(max(l, 6), 70) for l in longest]
            widths = [width * w / sum(weights) for w in weights]
            table = Table(data, colWidths=widths, repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f3b8a')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f5fb')]),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#c9d0e6')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(table)
        story.append(Spacer(1, 3 * mm))
    doc.build(story)
    return buffer.getvalue()