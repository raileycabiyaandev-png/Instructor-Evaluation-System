# ai_insights/services/analysis.py
#
# The MANAGER of an official AI analysis (one "AnalysisRun").
#
#   start_run()   - checks everything and creates the run record (no heavy work yet)
#   launch()      - does the work, in a background thread or right away
#   execute_run() - goes through the instructors one by one and keeps the progress up to date
#
# Rules it enforces:
#   * only a FINISHED period can be analysed (the same "finished" rule as the teacher pages)
#   * only ONE analysis can run at a time (also enforced by the database)
#   * the same data analysed with the same method is NOT processed again,
#     unless the administrator asks for a regeneration (that makes a new version number)
#   * one instructor failing never stops the others
#
# This file does not import scikit-learn. The heavy part (pipeline.py) is loaded only inside
# execute_run(), so the website still starts when the AI libraries are not installed.

import importlib
import logging
import threading
from datetime import timedelta

from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Count, Max, Sum
from django.utils import timezone

from evaluation.models import Evaluation

from .. import conf
from ..models import AnalysisRun
from .data import CATEGORIES, finished_periods, hash_of, period_label, periods_up_to

log = logging.getLogger(__name__)


class AnalysisBusy(Exception):
    """Another analysis is running. The message is safe to show."""


class AnalysisNotAllowed(Exception):
    """The chosen period cannot be analysed. The message is safe to show."""


def mark_stale_runs():
    """A run that says 'running' for too long was stopped by a restart or a crash. Close it."""
    cutoff = timezone.now() - timedelta(minutes=conf.STALE_RUN_MINUTES)
    AnalysisRun.objects.filter(status=AnalysisRun.STATUS_RUNNING, started_at__lt=cutoff).update(
        status=AnalysisRun.STATUS_FAILED, finished_at=timezone.now(),
        message="This analysis stopped before it finished (the server may have restarted). Run it again.")


def eligible_instructors(period):
    """
    (instructors, skipped_count). Eligible = active instructor with at least one evaluation in the period.
    Instructors with fewer than MIN_RESPONSES still get a row that says WHY there is no forecast.
    """
    from users.models import User
    counts = dict(Evaluation.objects.filter(period=period).values_list('teacher_id').annotate(c=Count('id')))
    active = list(User.objects.filter(role='instructor', is_active=True).order_by('id'))
    eligible = [t for t in active if counts.get(t.pk, 0) >= 1]
    return eligible, len(active) - len(eligible)


def data_fingerprint(period):
    """Short code of everything the analysis of `period` depends on. Same code = nothing new to do."""
    from users.models import User
    periods = periods_up_to(period)
    totals = Evaluation.objects.filter(period__in=periods).aggregate(
        n=Count('id'), newest=Max('id'), **{f: Sum(f) for f, _ in CATEGORIES})
    teachers = list(User.objects.filter(role='instructor', is_active=True).order_by('id').values_list('id', flat=True))
    return hash_of(
        period.pk, conf.FORECAST_MODEL_VERSION, conf.MIN_PERIODS, conf.MIN_RESPONSES, conf.MIN_COMMENTS,
        conf.max_validation_mae(), conf.sentiment_model_name(), sorted(totals.items()), teachers,
    )


def start_run(period, user=None, trigger='manual', regenerate=False, use_llm=False):
    """
    Create the run record. Returns (run, created).
    created=False means: this period was already analysed with the same data and method, so nothing was started.
    """
    if period.pk not in {p.pk for p in finished_periods()}:
        raise AnalysisNotAllowed("Only a finished evaluation period can be analysed. "
                                 "Wait until the period has closed.")
    eligible, _ = eligible_instructors(period)
    if not eligible:
        raise AnalysisNotAllowed("No instructor has any response in this period, so there is nothing to analyse.")

    mark_stale_runs()
    busy = AnalysisRun.objects.filter(status=AnalysisRun.STATUS_RUNNING).select_related('period').first()
    if busy:
        raise AnalysisBusy(f"An analysis for {period_label(busy.period)} is already running. Please wait for it to finish.")

    fingerprint = data_fingerprint(period)
    if not regenerate:
        same = (AnalysisRun.objects.filter(period=period, status__in=AnalysisRun.RELEASED,
                                           data_fingerprint=fingerprint)
                .order_by('-version').first())
        if same:
            return same, False

    try:
        with transaction.atomic():
            last = AnalysisRun.objects.filter(period=period).aggregate(m=Max('version'))['m'] or 0
            run = AnalysisRun.objects.create(
                period=period, version=last + 1, trigger=trigger, started_by=user,
                regenerate=regenerate, use_llm=use_llm, engine_version=conf.FORECAST_MODEL_VERSION,
                data_fingerprint=fingerprint, total_instructors=len(eligible))
    except IntegrityError:       # two clicks at the same moment: the database allows only one
        raise AnalysisBusy("Another analysis was just started. Please wait for it to finish.")
    return run, True


def execute_run(run_id, use_sentiment=True):
    """Do the work of one run and keep its progress up to date. Never raises."""
    run = AnalysisRun.objects.select_related('period').get(pk=run_id)
    try:
        pipeline = importlib.import_module('ai_insights.services.pipeline')   # loads scikit-learn here
        periods = periods_up_to(run.period)
        eligible, skipped = eligible_instructors(run.period)
        run.total_instructors, run.skipped = len(eligible), skipped
        run.save(update_fields=['total_instructors', 'skipped'])

        errors, no_model, ai_written = [], 0, 0
        for teacher in eligible:
            try:
                report = pipeline.run_for_instructor(
                    teacher, use_sentiment=use_sentiment, use_llm=run.use_llm, force=run.regenerate,
                    periods=periods, target_period=run.period, run=run)
                run.processed += 1
                if 'model_unavailable' in report['feedback'].values():
                    no_model += 1
                if report.get('llm', {}).get('summary') or report.get('llm', {}).get('suggestions'):
                    ai_written += 1
            except Exception:                      # one instructor must not stop the others
                log.exception("AI analysis failed for instructor %s", teacher.pk)
                run.failed += 1
                errors.append({'instructor_id': teacher.pk, 'message': 'Could not be processed. See the server log.'})
            run.errors = errors
            run.save(update_fields=['processed', 'failed', 'errors'])

        # the school-wide summary (counts only, never comments). A failure here must not stop the run.
        school_ai = False
        try:
            from . import school
            insight = school.generate(run.period, use_llm=run.use_llm, run=run)
            school_ai = bool(insight and insight.summary_source == 'ollama')
        except Exception:
            log.exception("School summary failed for run %s", run.pk)

        text = f"{run.processed} instructor(s) analysed"
        if run.failed:
            text += f", {run.failed} could not be processed"
        if run.skipped:
            text += f", {run.skipped} had no responses in this period"
        text += "."
        if no_model:
            text += (f" The comment model was not available for {no_model} instructor(s), "
                     f"so their comment summaries were skipped.")
        if run.use_llm:
            if ai_written:
                text += f" Ollama wrote the texts for {ai_written} instructor(s)."
            if ai_written < run.processed:
                text += (f" Rule-based text is shown for {run.processed - ai_written} instructor(s) "
                         f"(Ollama was off, too slow, or its answer failed the safety checks).")
        if run.use_llm and school_ai:
            text += " Ollama also wrote the school-wide summary."
        run.message = text
        run.status = AnalysisRun.STATUS_COMPLETED_ERRORS if run.failed else AnalysisRun.STATUS_COMPLETED
    except ImportError:
        run.status = AnalysisRun.STATUS_FAILED
        run.message = "The AI libraries are not installed (scikit-learn). Install them, then run it again."
    except Exception:
        log.exception("AI analysis run %s failed", run_id)
        run.status = AnalysisRun.STATUS_FAILED
        run.message = "The analysis failed. Nothing was released. See the server log."
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'message', 'finished_at'])
    return run


def _thread_main(run_id, use_sentiment):
    close_old_connections()
    try:
        execute_run(run_id, use_sentiment)
    finally:
        close_old_connections()


def launch(run, use_sentiment=True, background=None):
    """Start the work. Background thread by default (the page shows progress), or right now."""
    background = conf.run_in_background() if background is None else background
    if not background:
        return execute_run(run.pk, use_sentiment)
    thread = threading.Thread(target=_thread_main, args=(run.pk, use_sentiment), daemon=True,
                              name=f"ai-analysis-{run.pk}")
    thread.start()
    return thread


def unanalysed_periods():
    """Finished periods that have responses but no finished analysis yet (used by the scheduled command)."""
    done = set(AnalysisRun.objects.filter(status__in=AnalysisRun.RELEASED).values_list('period_id', flat=True))
    with_data = set(Evaluation.objects.values_list('period_id', flat=True).distinct())
    return [p for p in finished_periods() if p.pk in with_data and p.pk not in done]