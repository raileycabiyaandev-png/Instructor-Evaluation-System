# ai_insights/views.py
#
# Admin-only pages and the controls of the official analysis.
# Teachers do NOT get any AI view of their own here: their AI results are shown inside the
# existing teacher pages (users/views.py), using only request.user.

import importlib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import selectors
from .filters import parse_filters
from evaluation.models import EvaluationPeriod

from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404
from django.utils import timezone

from academics.models import Department
from users.models import User

from .models import Alert, AlertSettings, AnalysisRun, InstructorNotice, ReportLog
from .services import alerts as alert_service, analysis

# Pages the Generate button may send the admin back to (never a web address from the form)
BACK_PAGES = {'ai_admin_analytics', 'ai_admin_forecasting', 'ai_admin_feedback', 'ai_admin_recommendations',
              'ai_admin_trends', 'ai_admin_alerts'}


def _admin_only(user):
    if user.role != 'admin':
        raise PermissionDenied


def _keep(request):
    """The current filters as a web-address part (page number removed), for the pager links."""
    params = request.GET.copy()
    params.pop('page', None)
    return params.urlencode()


def _panel(request, default_period=None):
    """Data for the Generate panel. `default_period` = the period the page is showing (pre-selected)."""
    return selectors.generate_panel(request.GET.get('run_period') or (default_period.pk if default_period else None))


@login_required
@never_cache
def admin_analytics(request):
    _admin_only(request.user)
    filters = parse_filters(request.GET)
    data = selectors.overview_page(filters)
    items = []                                   # AI feedback summary of each instructor (summaries only, never comments)
    if data.get('period'):
        from dataclasses import replace
        items = selectors.feedback_page(replace(filters, period=data['period'], department=None, instructor=None))['all_items']
    return render(request, 'ai_insights/admin_analytics.html', {
        'filters': filters, 'keep': _keep(request), 'data': data, 'feedback_items': items,
        'panel': _panel(request, data.get('period')),
    })


@login_required
@never_cache
def admin_forecasting(request):
    """Performance Forecasting: saved forecasts only (reading never runs a model)."""
    _admin_only(request.user)
    filters = parse_filters(request.GET)
    return render(request, 'ai_insights/admin_forecasting.html', {
        'filters': filters, 'keep': _keep(request),
        'data': selectors.forecast_page(filters, request.GET.get('page', 1)),
        'panel': _panel(request),
    })


@login_required
@never_cache
def admin_feedback(request):
    """Student Feedback Analysis: saved summaries only. Instructor level, never a single comment."""
    _admin_only(request.user)
    filters = parse_filters(request.GET)
    return render(request, 'ai_insights/admin_feedback.html', {
        'filters': filters, 'keep': _keep(request),
        'data': selectors.feedback_page(filters, request.GET.get('tone', ''), request.GET.get('source', ''),
                                        request.GET.get('page', 1)),
        'panel': _panel(request),
    })


@login_required
@never_cache
def admin_recommendations(request):
    """Recommendations for every instructor (saved results only)."""
    _admin_only(request.user)
    filters = parse_filters(request.GET)
    return render(request, 'ai_insights/admin_recommendations.html', {
        'filters': filters, 'keep': _keep(request),
        'data': selectors.recommendations_page(
            filters, request.GET.get('priority', ''), request.GET.get('category', ''),
            request.GET.get('source', ''), request.GET.get('page', 1)),
        'panel': _panel(request),
    })


@login_required
@require_POST
def admin_generate(request):
    """
    "Generate AI Analysis": the administrator chooses a finished evaluation period and starts the
    official analysis of all eligible instructors. Instructors cannot call this (403).
    """
    _admin_only(request.user)
    back = request.POST.get('next')
    back = back if back in BACK_PAGES else 'ai_admin_analytics'

    try:
        period = EvaluationPeriod.objects.get(pk=int(request.POST.get('period', '')))
    except (ValueError, TypeError, EvaluationPeriod.DoesNotExist):
        messages.error(request, "Please choose an evaluation period first.")
        return redirect(back)

    # Load the AI code now, so a missing library gives a clear message and creates no run.
    try:
        importlib.import_module('ai_insights.services.pipeline')
    except ImportError:
        messages.error(request, "The AI libraries are not installed (scikit-learn). "
                                "Install them first, see AI_GUIDE.md. Nothing was started.")
        return redirect(back)

    regenerate = request.POST.get('regenerate') == 'on'
    use_llm = request.POST.get('use_llm') == 'on'
    try:
        run, created = analysis.start_run(period, user=request.user, trigger='manual',
                                          regenerate=regenerate, use_llm=use_llm)
    except analysis.AnalysisBusy as exc:
        messages.error(request, str(exc))
        return redirect(back)
    except analysis.AnalysisNotAllowed as exc:
        messages.error(request, str(exc))
        return redirect(back)

    if not created:
        messages.info(request, f"This period was already analysed with the same data (version {run.version}). "
                               f"Nothing new was processed. Tick \"Regenerate\" if you want to run it again.")
        return redirect(back)

    analysis.launch(run)
    run.refresh_from_db()
    if run.is_running:
        messages.success(request, f"Analysis version {run.version} started. This page shows its progress.")
    elif run.is_released:
        messages.success(request, f"Analysis version {run.version} finished. {run.message}")
    else:
        messages.error(request, f"Analysis version {run.version} did not finish. {run.message}")
    return redirect(back)


@login_required
@require_GET
@never_cache
def admin_run_status(request):
    """Progress of the newest analysis, as JSON for the progress bar. Admin only. No student data."""
    _admin_only(request.user)
    analysis.mark_stale_runs()
    run = AnalysisRun.objects.select_related('period').order_by('-started_at').first()
    if run is None:
        raise Http404
    summary = selectors.run_summary(run)
    return JsonResponse({
        key: (value.isoformat() if hasattr(value, 'isoformat') else value)
        for key, value in summary.items()
    })


# ─────────────────────────────────────────────
# Performance Trends
# ─────────────────────────────────────────────
@login_required
@never_cache
def admin_trends(request):
    _admin_only(request.user)
    filters = parse_filters(request.GET)
    return render(request, 'ai_insights/admin_trends.html', {
        'filters': filters, 'keep': _keep(request),
        'data': selectors.trends_page(filters, request.GET.get('metric', 'overall'), request.GET.get('page') or 1),
        'one': selectors.instructor_detail(filters.instructor) if filters.instructor else None,   # one instructor: feedback + suggestions
        'panel': _panel(request),
    })


# ─────────────────────────────────────────────
# Performance Alerts
# ─────────────────────────────────────────────
ALERT_KEEP = {'school_year', 'semester', 'period', 'department', 'instructor', 'status', 'severity', 'kind', 'page'}


def _back_to_alerts(request):
    """Return to the Alerts page with the SAME filters (only known keys are copied)."""
    posted = QueryDict(request.POST.get('qs', ''))
    params = QueryDict(mutable=True)
    for key in ALERT_KEEP:
        if posted.get(key):
            params[key] = posted.get(key)
    url = reverse('ai_admin_alerts')
    return redirect(f"{url}?{params.urlencode()}" if params else url)


@login_required
@never_cache
def admin_alerts(request):
    _admin_only(request.user)
    filters = parse_filters(request.GET)
    base = request.GET.copy()
    for key in ('status', 'page'):
        base.pop(key, None)
    return render(request, 'ai_insights/admin_alerts.html', {
        'filters': filters, 'keep': _keep(request), 'qs': request.GET.urlencode(), 'base_qs': base.urlencode(),
        'data': selectors.alerts_page(filters, request.GET.get('status', ''), request.GET.get('severity', ''),
                                      request.GET.get('kind', ''), request.GET.get('page', 1)),
        'panel': _panel(request),
    })


@login_required
@require_POST
def admin_alert_update(request, pk):
    _admin_only(request.user)
    alert = get_object_or_404(Alert, pk=pk)
    status = request.POST.get('status', '')
    if status not in selectors.ALERT_STATUS:
        messages.error(request, "That status is not valid.")
    else:
        alert.status = status
        alert.note = request.POST.get('note', '').strip()[:1000]
        if status == 'new':
            alert.reviewed_by, alert.reviewed_at = None, None
        else:
            alert.reviewed_by, alert.reviewed_at = request.user, timezone.now()
        alert.save()
        messages.success(request, "Alert updated.")
    if request.POST.get('back') == 'instructor':
        return redirect('ai_admin_instructor', pk=alert.instructor_id)
    return _back_to_alerts(request)


SETTINGS_RULES = {          # field: (smallest, largest, whole number?)
    'low_score': (1, 5, False), 'score_drop': (0.05, 2, False), 'falling_periods': (1, 5, True),
    'forecast_low': (1, 5, False), 'forecast_drop': (0.05, 2, False), 'negative_share': (5, 100, True),
    'category_low': (1, 5, False),
}


@login_required
@require_POST
def admin_alert_settings(request):
    _admin_only(request.user)
    values = {}
    for field, (low, high, whole) in SETTINGS_RULES.items():
        try:
            number = float(request.POST.get(field, ''))
        except ValueError:
            number = None
        if number is None or not (low <= number <= high) or number != number:
            messages.error(request, f"Not saved. Please enter a number between {low:g} and {high:g} for every limit.")
            return _back_to_alerts(request)
        values[field] = int(number) if whole else round(number, 2)
    row = AlertSettings.get()
    for field, value in values.items():
        setattr(row, field, value)
    row.updated_by = request.user
    row.save()
    count = alert_service.resync_all()
    messages.success(request, f"Limits saved. Alerts were updated from the saved results ({count} active).")
    return _back_to_alerts(request)


# ─────────────────────────────────────────────
# One instructor, everything together
# ─────────────────────────────────────────────
@login_required
@never_cache
def admin_instructor(request, pk):
    _admin_only(request.user)
    teacher = get_object_or_404(User.objects.select_related('department'), pk=pk, role='instructor')
    return render(request, 'ai_insights/admin_instructor.html', {
        'data': selectors.instructor_detail(teacher), 'statuses': selectors.ALERT_STATUS,
    })


# ─────────────────────────────────────────────
# Reports (PDF / Excel)
# ─────────────────────────────────────────────
REPORT_KINDS = {'school': 'School report', 'department': 'Department report', 'instructor': 'Instructor report'}


@login_required
@never_cache
def admin_reports(request):
    _admin_only(request.user)
    from .services import reports
    filters = parse_filters(request.GET)
    level = 'instructor' if filters.instructor else ('department' if filters.department else 'school')
    period = selectors.scope_period(filters)
    return render(request, 'ai_insights/admin_reports.html', {
        'filters': filters, 'kinds': REPORT_KINDS, 'level': level, 'period': period,
        'period_text': (f"{period.school_year} · {period.get_semester_display()} · {period.get_term_display()}" if period else ''),
        'history': ReportLog.objects.select_related('user')[:10],
        'panel': _panel(request),
    })


def _send_report(request, report, kind, fmt):
    from .services import reports
    try:
        data = reports.to_pdf(report) if fmt == 'pdf' else reports.to_xlsx(report)
    except reports.ReportLibraryMissing as exc:
        messages.error(request, str(exc))
        return None
    ReportLog.objects.create(user=request.user, kind=kind, fmt=fmt, title=report['title'][:200])
    mime = 'application/pdf' if fmt == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    response = HttpResponse(data, content_type=mime)
    response['Content-Disposition'] = f'attachment; filename="{reports.file_name(report, fmt)}"'
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def admin_report_download(request):
    """Admin only. The report follows the filters sent with the form (all checked by parse_filters)."""
    _admin_only(request.user)
    from .services import reports
    kind, fmt = request.POST.get('kind', ''), request.POST.get('fmt', '')
    if kind == 'summary':                       # old name
        kind = 'school'
    if kind not in REPORT_KINDS or fmt not in ('pdf', 'xlsx'):
        messages.error(request, "Please choose a report type and a file type.")
        return redirect('ai_admin_reports')
    filters = parse_filters(request.POST)
    if kind == 'instructor' and filters.instructor is None:
        messages.error(request, "Please choose an instructor for this report.")
        return redirect('ai_admin_reports')
    report = reports.analytics_report(filters, kind)
    response = _send_report(request, report, kind, fmt)
    return response or redirect('ai_admin_reports')


@login_required
@require_POST
def my_report(request):
    """
    An instructor's OWN report (PDF or Excel), for one finished period that has answers.
    Real scores need no AI. AI parts are included only if the official analysis already exists.
    """
    if request.user.role != 'instructor':
        raise PermissionDenied
    import datetime

    from evaluation.models import Evaluation, EvaluationPeriod

    from .services import reports
    from .services.data import finished_periods
    fmt = request.POST.get('fmt', '')
    own = [p for p in finished_periods() if Evaluation.objects.filter(teacher=request.user, period=p).exists()]
    wanted = request.POST.get('period', '')
    period = next((p for p in own if str(p.pk) == wanted), None) if wanted else (own[-1] if own else None)
    if fmt not in ('pdf', 'xlsx') or period is None:
        messages.error(request, "Please choose a finished period and a file type.")
        return redirect('instructor_reports')
    release = selectors.latest_release()
    cutoff = release.finished_at if release else timezone.make_aware(datetime.datetime(1970, 1, 1))
    report = reports.instructor_report(request.user, cutoff=cutoff, admin=False, period=period)
    return _send_report(request, report, 'my_report', fmt) or redirect('instructor_reports')


@login_required
@require_POST
def notices_read(request):
    if request.user.role != 'instructor':
        raise PermissionDenied
    InstructorNotice.objects.filter(instructor=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect(f"{reverse('instructor_dashboard')}?section=updates")