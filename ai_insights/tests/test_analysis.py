# Stage A tests: the official analysis (AnalysisRun), the Generate button, instructor access,
# the forecasting rules (baseline, not reliable, term order) and the scheduled command.

import sys
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from academics.models import Department
from ai_insights import selectors
from ai_insights.models import AnalysisRun, ForecastResult, Suggestion
from ai_insights.services import analysis, pipeline
from ai_insights.services.data import analyse_history, finished_periods
from evaluation.models import EvaluationPeriod

from .factories import build_history, make_period, make_user, rate, release, spread


class Base(TestCase):
    def setUp(self):
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.alice = make_user('alice@test.com', 'instructor', department=self.it)
        self.bob = make_user('bob@test.com', 'instructor', department=self.it)
        self.admin = make_user('admin@test.com', 'admin')
        self.student = make_user('stud@test.com', 'student')
        # alice: 5 finished periods, rising. bob: the same 5 periods, a little lower.
        self.periods = build_history(self.alice, [3.0, 3.25, 3.5, 3.75, 4.0])
        for period, score in zip(self.periods, [2.5, 2.75, 3.0, 3.25, 3.5]):
            rate(EvaluationPeriod.objects.get(pk=period.pk), self.bob, spread(score, 4))
        self.p = self.periods                                  # shortcut

    def login(self, user):
        self.client.force_login(user)

    def run_sync(self, period, **kw):
        run, created = analysis.start_run(period, user=self.admin, **kw)
        if created:
            analysis.launch(run, use_sentiment=False, background=False)
            run.refresh_from_db()
        return run, created


# ─────────────────────────── the run manager ───────────────────────────
class StartRunTests(Base):
    def test_only_finished_periods_can_be_analysed(self):
        live = make_period(5, active=True)
        rate(live, self.alice, [4, 4, 4])
        with self.assertRaises(analysis.AnalysisNotAllowed):
            analysis.start_run(live, user=self.admin)

    def test_a_period_without_any_response_cannot_be_analysed(self):
        empty = make_period(5)
        with self.assertRaises(analysis.AnalysisNotAllowed):
            analysis.start_run(empty, user=self.admin)

    def test_same_data_is_not_processed_again(self):
        first, created = self.run_sync(self.p[4])
        self.assertTrue(created)
        self.assertEqual(first.status, 'completed')
        again, created = analysis.start_run(self.p[4], user=self.admin)
        self.assertFalse(created)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(AnalysisRun.objects.count(), 1)

    def test_regenerate_makes_a_new_version_and_keeps_the_old_one(self):
        first, _ = self.run_sync(self.p[4])
        second, created = self.run_sync(self.p[4], regenerate=True)
        self.assertTrue(created)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(AnalysisRun.objects.filter(period=self.p[4]).count(), 2)
        self.assertEqual(second.engine_version, 'forecast-v3')

    def test_new_data_makes_a_new_run_without_asking(self):
        first, _ = self.run_sync(self.p[4])
        rate(EvaluationPeriod.objects.get(pk=self.p[4].pk), self.alice, [1])     # one more response
        second, created = analysis.start_run(self.p[4], user=self.admin)
        self.assertTrue(created)
        self.assertEqual(second.version, 2)

    def test_only_one_run_at_a_time(self):
        run, _ = analysis.start_run(self.p[4], user=self.admin)          # created, still "running"
        self.assertTrue(run.is_running)
        with self.assertRaises(analysis.AnalysisBusy):
            analysis.start_run(self.p[3], user=self.admin)

    def test_the_database_also_refuses_two_running_runs(self):
        analysis.start_run(self.p[4], user=self.admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnalysisRun.objects.create(period=self.p[3], version=1, status='running')

    def test_a_stuck_run_is_closed_and_does_not_block_new_runs(self):
        run, _ = analysis.start_run(self.p[4], user=self.admin)
        AnalysisRun.objects.filter(pk=run.pk).update(started_at=timezone.now() - timezone.timedelta(hours=2))
        new, created = analysis.start_run(self.p[3], user=self.admin)
        self.assertTrue(created)
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertIn('stopped', run.message)


class ExecuteRunTests(Base):
    def test_progress_counts_and_status(self):
        run, _ = self.run_sync(self.p[4])
        self.assertEqual((run.total_instructors, run.processed, run.failed), (2, 2, 0))
        self.assertEqual(run.percent, 100)
        self.assertEqual(run.status, 'completed')
        self.assertIsNotNone(run.finished_at)

    def test_instructors_without_responses_in_the_period_are_skipped_and_counted(self):
        quiet = make_user('quiet@test.com', 'instructor')
        run, _ = self.run_sync(self.p[4])
        self.assertEqual(run.skipped, 1)
        self.assertFalse(ForecastResult.objects.filter(instructor=quiet).exists())

    def test_one_failing_instructor_does_not_stop_the_others(self):
        real = pipeline.run_for_instructor

        def flaky(teacher, **kw):
            if teacher.pk == self.alice.pk:
                raise RuntimeError('boom')
            return real(teacher, **kw)

        with mock.patch.object(pipeline, 'run_for_instructor', side_effect=flaky):
            run, _ = self.run_sync(self.p[4])
        self.assertEqual(run.status, 'completed_errors')
        self.assertEqual((run.processed, run.failed), (1, 1))
        self.assertTrue(run.is_released)                       # the rest is still released
        self.assertTrue(ForecastResult.objects.filter(instructor=self.bob, status='ok').exists())
        self.assertNotIn('Traceback', str(run.errors))

    def test_missing_ai_library_marks_the_run_failed_with_a_clear_message(self):
        run, _ = analysis.start_run(self.p[4], user=self.admin)
        with mock.patch.dict(sys.modules, {'ai_insights.services.pipeline': None}):
            analysis.launch(run, background=False)
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertIn('not installed', run.message)
        self.assertFalse(run.is_released)

    def test_forecast_is_based_on_the_chosen_period_and_never_uses_later_data(self):
        later = make_period(5)
        rate(later, self.alice, [5, 5, 5, 5])                  # a much higher LATER period
        self.run_sync(self.p[4])
        row = ForecastResult.objects.get(instructor=self.alice, based_on_period=self.p[4])
        self.assertEqual(row.status, 'ok')
        self.assertEqual(len(row.history), 5)                  # the later period is not in it
        self.assertAlmostEqual(row.history[-1]['overall'], 4.0)
        self.assertLess(row.predicted_overall, 4.6)            # not pulled up by the later 5.0

    def test_chosen_period_with_too_few_responses_gives_a_clear_message_not_an_old_forecast(self):
        thin = make_period(5)
        rate(thin, self.alice, [4, 4, 4, 4])
        rate(thin, self.bob, [4, 4])                           # only 2 responses
        self.run_sync(thin)
        row = ForecastResult.objects.get(instructor=self.bob, based_on_period=thin)
        self.assertEqual(row.status, 'insufficient')
        self.assertIsNone(row.predicted_overall)
        self.assertIn('fewer than 3 responses', row.message)
        self.assertFalse(Suggestion.objects.filter(instructor=self.bob, period=thin).exists())
        self.assertTrue(ForecastResult.objects.filter(instructor=self.alice, based_on_period=thin, status='ok').exists())

    def test_ratings_are_never_changed_by_an_analysis(self):
        from evaluation.models import Evaluation
        before = list(Evaluation.objects.order_by('id').values_list('id', 'teaching_effectiveness', 'comment'))
        self.run_sync(self.p[4])
        self.assertEqual(before, list(Evaluation.objects.order_by('id').values_list('id', 'teaching_effectiveness', 'comment')))


class ForecastRuleTests(Base):
    def test_one_period_is_a_baseline_not_a_forecast(self):
        newbie = make_user('newbie@test.com', 'instructor')
        rate(EvaluationPeriod.objects.get(pk=self.p[4].pk), newbie, [4, 4, 4, 5])   # the ONLY finished period
        self.run_sync(self.p[4])
        row = ForecastResult.objects.get(instructor=newbie)
        self.assertEqual(row.status, 'baseline')
        self.assertIsNone(row.predicted_overall)
        self.assertEqual(row.based_on_period, self.p[4])
        self.assertIn('Baseline only', row.message)
        self.assertEqual(row.history[0]['overall'], 4.25)                              # the real baseline score

    def test_low_response_periods_are_left_out_and_reported(self):
        mixed = make_user('mixed@test.com', 'instructor')
        for i, n in enumerate([4, 2, 4, 4, 4]):
            rate(EvaluationPeriod.objects.get(pk=self.p[i].pk), mixed, [4] * n)
        series, quality = analyse_history(mixed, finished_periods())
        self.assertEqual(len(series), 4)
        self.assertEqual(quality['periods_found'], 5)
        self.assertEqual(quality['periods_used'], 4)
        self.assertEqual(quality['skipped'][0]['responses'], 2)
        self.run_sync(self.p[4])
        row = ForecastResult.objects.get(instructor=mixed, based_on_period=self.p[4])
        self.assertEqual(row.data_quality['skipped'][0]['reason'], 'fewer than 3 responses')

    def test_an_unreliable_model_shows_no_number_anywhere(self):
        wild = make_user('wild@test.com', 'instructor', department=self.it)
        for i, score in enumerate([1, 5, 1, 5, 1]):
            rate(EvaluationPeriod.objects.get(pk=self.p[i].pk), wild, [score] * 4)
        self.run_sync(self.p[4])
        row = ForecastResult.objects.get(instructor=wild)
        self.assertEqual(row.status, 'unreliable')
        self.assertIsNone(row.predicted_overall)
        release(self.admin, self.p[4])
        self.login(wild)
        html = self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'}).content.decode()
        self.assertIn('allowed limit', html)
        self.assertNotIn('Predicted change', html)
        self.login(self.admin)
        page = self.client.get(reverse('ai_admin_forecasting')).content.decode()
        self.assertIn('Not reliable enough', page)

    def test_the_forecast_shows_the_target_period_and_method_version(self):
        self.run_sync(self.p[4])
        row = ForecastResult.objects.get(instructor=self.alice, based_on_period=self.p[4])
        # period 4 is Prelims of the 2nd semester, so the next one is the Midterm of the same semester
        self.assertEqual((row.target['term'], row.target['semester'], row.target['school_year']),
                         ('midterm', '2nd', '2024-2025'))
        self.assertEqual(row.model_version, 'forecast-v3')
        self.assertTrue(row.target_label.startswith('Midterm'))


# ─────────────────────────── instructors see only their own, only after release ───────────────────────────
class ReleaseGateTests(Base):
    def saved_but_not_released(self):
        pipeline.run_for_all(use_sentiment=False)               # results exist, but no official run yet

    def test_instructor_sees_a_status_message_until_the_admin_generates(self):
        self.saved_but_not_released()
        self.login(self.alice)
        response = self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'})
        html = response.content.decode()
        self.assertIn('has not been generated yet by your administrator', html)
        self.assertNotIn('Predicted change', html)
        self.assertFalse(response.context['ai']['released'])

    def test_instructor_without_responses_in_the_analysed_period_is_told_why(self):
        # carol has only an OLD period; the admin analyses the newest one, where carol has no responses
        carol = make_user('carol@test.com', 'instructor', department=self.it)
        rate(EvaluationPeriod.objects.get(pk=self.p[0].pk), carol, [4, 4, 4, 4])
        self.run_sync(self.p[4])
        self.login(carol)
        response = self.client.get(reverse('instructor_dashboard'))
        ai = response.context['ai']
        self.assertEqual(ai['forecast']['state'], 'not_included')
        self.assertEqual(ai['feedback']['state'], 'not_included')
        html = response.content.decode()
        self.assertIn('did not include you', html)
        self.assertNotIn('has not been generated yet by your administrator', html)
        self.assertNotIn('Predicted change', html)

    def test_results_appear_automatically_after_the_official_run_finishes(self):
        self.run_sync(self.p[4])
        self.login(self.alice)
        html = self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'}).content.decode()
        self.assertIn('AI prediction', html)

    def test_results_made_after_the_last_release_stay_hidden_until_the_next_official_run(self):
        self.run_sync(self.p[4])
        last = make_period(5)
        rate(last, self.alice, [5, 5, 5, 5])
        pipeline.run_for_instructor(self.alice, use_sentiment=False,
                                    periods=finished_periods(), target_period=last)
        block = selectors.teacher_dashboard_ai(self.alice)['forecast']
        self.assertEqual(block['row'].based_on_period, self.p[4])        # still the official one
        self.run_sync(last)
        block = selectors.teacher_dashboard_ai(self.alice)['forecast']
        self.assertEqual(block['row'].based_on_period, last)

    def test_results_stay_visible_while_the_admin_regenerates(self):
        # "Regenerate" rewrites the saved rows (new time stamp) before the run is finished.
        # The instructor must keep seeing the results of the already released period meanwhile.
        self.run_sync(self.p[4])
        run, _ = analysis.start_run(self.p[4], user=self.admin, regenerate=True)     # status: running
        pipeline.run_for_instructor(self.alice, use_sentiment=False, force=True,
                                    periods=analysis.periods_up_to(self.p[4]), target_period=self.p[4], run=run)
        self.assertEqual(AnalysisRun.objects.get(pk=run.pk).status, 'running')
        ai = selectors.teacher_dashboard_ai(self.alice)
        self.assertEqual(ai['forecast']['state'], 'ok')
        self.assertEqual(ai['suggestions']['state'], 'ok')
        self.login(self.alice)
        response = self.client.get(reverse('instructor_evaluation_results'), {'tab': 'ai'})
        self.assertEqual(response.context['period_ai']['suggestions']['state'], 'ok')
        self.assertEqual(response.context['ai']['forecast']['state'], 'ok')

    def test_one_broken_block_does_not_empty_the_other_blocks(self):
        self.run_sync(self.p[4])
        with mock.patch.object(selectors, '_forecast_block', side_effect=RuntimeError('boom')):
            ai = selectors.teacher_dashboard_ai(self.alice)
        self.assertEqual(ai['forecast']['state'], 'error')          # says so, does not crash
        self.assertEqual(ai['suggestions']['state'], 'ok')          # the rest still shows
        self.assertFalse(ai.get('error'))

    def test_dashboard_tabs_are_real_links_and_the_chosen_tab_is_drawn_by_the_server(self):
        self.run_sync(self.p[4])
        self.login(self.alice)
        for section, marker in (('forecast', 'AI prediction'), ('suggestions', 'ac-fb-item'), ('feedback', 'ac-body')):
            html = self.client.get(reverse('instructor_dashboard'), {'section': section}).content.decode()
            self.assertIn('href="?section=forecast#ai-results"', html)
            self.assertIn(marker, html)
            self.assertNotIn('data-ai-panel', html)   # no panel waits for JavaScript

    def test_a_failed_run_releases_nothing(self):
        run, _ = analysis.start_run(self.p[4], user=self.admin)
        with mock.patch.dict(sys.modules, {'ai_insights.services.pipeline': None}):
            analysis.launch(run, background=False)
        self.login(self.alice)
        self.assertFalse(self.client.get(reverse('instructor_dashboard')).context['ai']['released'])

    def test_instructor_never_sees_another_instructors_results_after_release(self):
        self.run_sync(self.p[4])
        bob_row = ForecastResult.objects.get(instructor=self.bob, based_on_period=self.p[4])
        self.login(self.alice)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertNotIn('Bob', html)
        self.assertNotIn(f"{bob_row.predicted_overall:.2f}", html)
        self.assertEqual(self.client.get(reverse('instructor_dashboard')).context['ai']['forecast']['row'].instructor,
                         self.alice)

    def test_instructors_and_students_cannot_start_or_watch_an_analysis(self):
        for user in (self.alice, self.student):
            self.login(user)
            self.assertEqual(self.client.post(reverse('ai_admin_generate'), {'period': self.p[4].pk}).status_code, 403)
            self.assertEqual(self.client.get(reverse('ai_admin_run_status')).status_code, 403)
            self.assertEqual(self.client.get(reverse('ai_admin_forecasting')).status_code, 403)
        self.assertEqual(AnalysisRun.objects.count(), 0)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('ai_admin_generate'), {'period': self.p[4].pk}).status_code, 302)

    def test_instructor_pages_have_no_generate_button(self):
        self.run_sync(self.p[4])
        self.login(self.alice)
        for name in ('instructor_dashboard', 'instructor_evaluation_results'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('Generate analysis', html)
            self.assertNotIn(reverse('ai_admin_generate'), html)

    def test_results_page_also_waits_for_the_release(self):
        self.saved_but_not_released()
        self.login(self.alice)
        response = self.client.get(reverse('instructor_evaluation_results'), {'tab': 'ai'})
        self.assertFalse(response.context['ai']['released'])
        self.assertEqual(response.context['ai']['feedback']['state'], 'not_released')


# ─────────────────────────── the admin buttons and pages ───────────────────────────
@override_settings(AI_RUN_IN_BACKGROUND=False)
class GenerateViewTests(Base):
    def post(self, **data):
        self.login(self.admin)
        return self.client.post(reverse('ai_admin_generate'), data, follow=True)

    def test_admin_generates_for_the_chosen_period(self):
        response = self.post(period=self.p[4].pk, next='ai_admin_forecasting')
        self.assertContains(response, 'Analysis version 1 finished')
        run = AnalysisRun.objects.get()
        self.assertEqual((run.period, run.started_by, run.trigger), (self.p[4], self.admin, 'manual'))
        self.assertContains(response, 'Regenerate analysis')

    def test_second_click_does_not_process_again_but_regenerate_does(self):
        self.post(period=self.p[4].pk)
        response = self.post(period=self.p[4].pk)
        self.assertContains(response, 'Nothing new was processed')
        self.assertEqual(AnalysisRun.objects.count(), 1)
        response = self.post(period=self.p[4].pk, regenerate='on')
        self.assertContains(response, 'version 2')
        self.assertEqual(AnalysisRun.objects.count(), 2)

    def test_no_period_chosen_or_a_made_up_one_is_a_clear_error(self):
        self.assertContains(self.post(), 'choose an evaluation period')
        self.assertContains(self.post(period='99999'), 'choose an evaluation period')
        self.assertContains(self.post(period='abc'), 'choose an evaluation period')
        self.assertEqual(AnalysisRun.objects.count(), 0)

    def test_open_period_is_refused(self):
        live = make_period(5, active=True)
        rate(live, self.alice, [4, 4, 4])
        self.assertContains(self.post(period=live.pk), 'finished evaluation period')

    def test_busy_message(self):
        analysis.start_run(self.p[4], user=self.admin)
        self.assertContains(self.post(period=self.p[3].pk), 'already running')

    def test_missing_library_creates_no_run(self):
        with mock.patch.dict(sys.modules, {'ai_insights.services.pipeline': None}):
            response = self.post(period=self.p[4].pk)
        self.assertContains(response, 'AI libraries are not installed')
        self.assertEqual(AnalysisRun.objects.count(), 0)

    def test_status_json_has_progress_and_no_personal_data(self):
        self.post(period=self.p[4].pk)
        data = self.client.get(reverse('ai_admin_run_status')).json()
        self.assertEqual((data['status'], data['processed'], data['total'], data['percent']), ('completed', 2, 2, 100))
        text = str(data)
        for secret in ('alice', 'Alice', 'bob@', 'stud@test.com', 'secret-school'):
            self.assertNotIn(secret, text)

    def test_status_json_with_no_runs_is_404(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('ai_admin_run_status')).status_code, 404)

    def test_panel_lists_periods_with_their_state_and_shows_the_running_bar(self):
        self.login(self.admin)
        html = self.client.get(reverse('ai_admin_analytics')).content.decode()
        self.assertIn('Generate analysis', html)
        self.assertIn('not analysed yet', html)
        self.assertIn('Not Yet Generated', html)
        analysis.start_run(self.p[4], user=self.admin)                       # now "running"
        html = self.client.get(reverse('ai_admin_forecasting')).content.decode()
        self.assertIn('Running', html)
        self.assertIn('role="progressbar"', html)

    def test_panel_says_when_new_data_arrived_after_the_analysis(self):
        self.post(period=self.p[4].pk)
        rate(EvaluationPeriod.objects.get(pk=self.p[4].pk), self.alice, [2])
        self.login(self.admin)
        self.assertIn('new data since', self.client.get(reverse('ai_admin_analytics')).content.decode())

    def test_admin_pages_show_nothing_personal_about_students(self):
        self.post(period=self.p[4].pk)
        for name in ('ai_admin_analytics', 'ai_admin_forecasting'):
            html = self.client.get(reverse(name)).content.decode()
            for secret in ('Secretname', 'secret-school.com', 'stud@test.com'):
                self.assertNotIn(secret, html)
            for bad in ('{#', '#}', '{%'):
                self.assertNotIn(bad, html)


class BackgroundTests(TransactionTestCase):
    """The real button starts a background thread. Check that it finishes and records the result."""

    def test_background_run_finishes_and_is_recorded(self):
        it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        alice = make_user('alice@test.com', 'instructor', department=it)
        admin = make_user('admin@test.com', 'admin')
        periods = build_history(alice, [3.0, 3.25, 3.5, 3.75])
        run, created = analysis.start_run(periods[-1], user=admin)
        thread = analysis.launch(run, use_sentiment=False, background=True)
        thread.join(60)
        self.assertFalse(thread.is_alive())
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.processed, 1)


# ─────────────────────────── the scheduled command ───────────────────────────
class AutoCommandTests(Base):
    def cmd(self, *args):
        out = StringIO()
        call_command('run_ai_insights', '--no-sentiment', *args, stdout=out)
        return out.getvalue()

    def test_auto_analyses_every_finished_period_that_has_no_analysis_and_then_stops(self):
        text = self.cmd('--auto')
        self.assertEqual(AnalysisRun.objects.filter(status='completed').count(), 5)
        self.assertEqual(set(AnalysisRun.objects.values_list('trigger', flat=True)), {'auto'})
        self.assertIn('Completed', text)
        self.assertIn('Nothing to do', self.cmd('--auto'))

    def test_auto_ignores_periods_that_are_still_open(self):
        live = make_period(5, active=True)
        rate(live, self.alice, [4, 4, 4])
        self.cmd('--auto')
        self.assertFalse(AnalysisRun.objects.filter(period=live).exists())

    def test_admin_can_still_regenerate_after_an_automatic_run(self):
        self.cmd('--auto')
        run, created = self.run_sync(self.p[4], regenerate=True)
        self.assertTrue(created)
        self.assertEqual(run.version, 2)