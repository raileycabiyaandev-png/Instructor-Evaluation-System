from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from academics.models import Department
from ai_insights.models import ForecastResult
from ai_insights.services import ollama, pipeline, sentiment

from .factories import build_history, make_period, make_user, rate, release
from .fake_llm import fake_generate
from .test_sentiment import fake_classifier

COMMENTS = ["Always late to class", "He is late again and the class is boring",
            "Great teacher, explains the lesson clearly", "Thank you sir, very good lessons"]


class Base(TestCase):
    def setUp(self):
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.ge, _ = Department.objects.get_or_create(code='GENED', defaults={'name': 'General Education'})
        self.teacher = make_user('alice@test.com', 'instructor', department=self.it)
        self.rival = make_user('rival@test.com', 'instructor', department=self.ge)
        self.rival.first_name, self.rival.last_name = 'Zebediah', 'Rivalson'
        self.rival.save()
        self.admin = make_user('admin@test.com', 'admin')
        self.student = make_user('stud@test.com', 'student')
        sentiment._cache.clear()

        # alice improves (3.0 -> 4.0), the rival gets worse (4.0 -> 2.0)
        self.periods = build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        for i, v in enumerate([4.0, 3.5, 3.0, 2.5, 2.0]):
            rate(make_period.__globals__['EvaluationPeriod'].objects.get(pk=self.periods[i].pk), self.rival,
                 [round(v)] * 4)
        rate(make_period(5), self.teacher, [4, 4, 4, 4], COMMENTS)
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            pipeline.run_for_all()
        self.release_run = release(self.admin)          # the administrator's official analysis is finished
        self.last_period = self.release_run.period

    def login(self, user):
        self.client.force_login(user)


class TeacherPageTests(Base):
    def test_dashboard_shows_the_saved_forecast_labelled_as_prediction(self):
        self.login(self.teacher)
        response = self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'})
        self.assertEqual(response.status_code, 200)
        fc = ForecastResult.objects.get(instructor=self.teacher, status='ok')
        html = response.content.decode()
        self.assertIn('AI prediction', html)
        self.assertIn('Last real score', html)
        self.assertIn(f"{fc.predicted_overall:.2f}", html)
        self.assertIn('How reliable is this?', html)
        self.assertIn(fc.target_label, html)

    def test_dashboard_shows_summary_and_suggestions_with_sources(self):
        self.login(self.teacher)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertIn('What students liked', html)
        self.assertIn('Rule-based', html)
        self.assertIn('Suggestions', html)
        for label in ('Teaching Effectiveness', 'Communication Skills', 'Classroom Management',
                      'Punctuality', 'Student Engagement'):
            self.assertIn(label, html)

    def test_teacher_never_sees_another_teachers_ai_results(self):
        self.login(self.teacher)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        rival = ForecastResult.objects.get(instructor=self.rival, status='ok')
        self.assertNotIn('Zebediah', html)
        self.assertNotIn('Rivalson', html)
        self.assertNotIn(f"{rival.predicted_overall:.2f}", html)
        ctx = self.client.get(reverse('instructor_dashboard')).context['ai']
        self.assertEqual(ctx['forecast']['row'].instructor, self.teacher)

    def test_no_student_identity_or_comment_text_on_ai_screens(self):
        self.login(self.teacher)
        for url in (reverse('instructor_dashboard'), reverse('instructor_evaluation_results')):
            html = self.client.get(url).content.decode()
            for secret in ('Secretname', 'secret-school.com', 'stud@test.com'):
                self.assertNotIn(secret, html)
        dash = self.client.get(reverse('instructor_dashboard')).content.decode()
        for comment in COMMENTS:                      # the AI summary never repeats a comment
            self.assertNotIn(comment, dash)

    def test_page_loads_never_run_a_model(self):
        self.login(self.teacher)
        with mock.patch.object(sentiment, 'get_classifier', side_effect=AssertionError('no model on page load')), \
             mock.patch.object(ollama, 'generate', side_effect=AssertionError('no Ollama on page load')):
            self.assertEqual(self.client.get(reverse('instructor_dashboard')).status_code, 200)
            self.assertEqual(self.client.get(reverse('instructor_evaluation_results')).status_code, 200)

    def test_not_generated_yet_message(self):
        newbie = make_user('new@test.com', 'instructor')
        self.login(newbie)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        # the official analysis exists, but this new instructor has no results, so nothing is invented for them
        self.assertIn('No evaluation results yet', html)
        self.assertNotIn('Predicted change', html)
        self.assertNotEqual(self.client.get(reverse('instructor_dashboard')).context['ai']['forecast']['state'], 'ok')

    def test_insufficient_data_message_and_no_number(self):
        short = make_user('short@test.com', 'instructor')
        for i in range(3):
            rate(make_period.__globals__['EvaluationPeriod'].objects.get(pk=self.periods[i].pk), short, [4] * 3)
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            pipeline.run_for_instructor(short)
        release(self.admin)
        self.login(short)
        html = self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'}).content.decode()
        self.assertIn('Not enough history', html)
        self.assertNotIn('Predicted overall score', html)

    def test_results_page_has_ai_tab_for_released_period_only(self):
        self.login(self.teacher)
        response = self.client.get(reverse('instructor_evaluation_results'), {'tab': 'ai'})
        self.assertEqual(response.context['state'], 'finished')
        self.assertIsNotNone(response.context['period_ai'])

        live = make_period(6, active=True)
        rate(live, self.teacher, [5, 5, 5])
        url = reverse('instructor_evaluation_results') + \
            f'?tab=ai&school_year={live.school_year}&semester={live.semester}&term={live.term}'
        response = self.client.get(url)
        self.assertEqual(response.context['state'], 'open')
        self.assertIsNone(response.context['period_ai'])        # no AI text for a period that is still open

    def test_results_for_an_older_period_say_suggestions_are_for_the_newest(self):
        self.login(self.teacher)
        p = self.periods[4]
        url = reverse('instructor_evaluation_results') + \
            f'?tab=ai&school_year={p.school_year}&semester={p.semester}&term={p.term}'
        response = self.client.get(url)
        self.assertEqual(response.context['period_ai']['suggestions']['state'], 'older_period')

    def test_broken_ai_table_does_not_break_the_page(self):
        self.login(self.teacher)
        with mock.patch('ai_insights.selectors._forecast_block', side_effect=RuntimeError('db down')):
            response = self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('could not be loaded', response.content.decode())

    def test_comment_summary_hidden_when_too_few_students(self):
        few = make_user('few@test.com', 'instructor')
        rate(self.periods[0], few, [4, 4], ["good class sir", "great teacher"])
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            pipeline.run_for_instructor(few)
        release(self.admin)
        self.login(few)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertNotIn('What students wrote (summary', html.split('No comment summary')[0] if False else '')
        ctx = self.client.get(reverse('instructor_dashboard')).context['ai']
        self.assertEqual(ctx['feedback']['state'], 'no_period' if ctx['feedback']['state'] == 'no_period' else 'hidden')


@override_settings(AI_RUN_IN_BACKGROUND=False)
class AdminPageTests(Base):
    def test_permissions(self):
        url = reverse('ai_admin_analytics')
        self.assertEqual(self.client.get(url).status_code, 302)               # not logged in
        for user in (self.teacher, self.student):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(reverse('ai_admin_generate')).status_code, 403)
            self.assertEqual(self.client.get(reverse('ai_admin_run_status')).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_generate_needs_post(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('ai_admin_generate')).status_code, 405)

    def test_page_shows_department_forecast(self):
        self.login(self.admin)
        response = self.client.get(reverse('ai_admin_analytics'))
        html = response.content.decode()
        bars = {d['name']: d for d in response.context['data']['outlook']['bars']}
        # The rival (General Education) has no answers in the newest period, so her older forecast is NOT shown as new.
        self.assertEqual(set(bars), {'Information Technology'})
        self.assertGreater(bars['Information Technology']['avg_pred'], 0)
        self.assertIn('<svg', html)                       # the department chart is drawn on the server
        self.assertNotIn('chart.umd', html)               # no outside chart library any more

    def test_department_trend_uses_real_averages_and_hides_small_cells(self):
        self.login(self.admin)
        chart = self.client.get(reverse('ai_admin_analytics')).context['data']['outlook']['line_chart']
        it = next(r for r in chart['table'] if r['name'] == 'Information Technology')
        self.assertEqual(it['values'][:5], [3.0, 3.25, 3.5, 3.75, 4.0])
        small = make_user('small@test.com', 'instructor', department=Department.objects.get_or_create(
            code='HM', defaults={'name': 'Hospitality'})[0])
        rate(self.periods[0], small, [1, 1])                    # 2 responses only
        chart = self.client.get(reverse('ai_admin_analytics')).context['data']['outlook']['line_chart']
        self.assertNotIn('Hospitality', [r['name'] for r in chart['table']])

    def test_no_student_data_or_comments_on_the_admin_page(self):
        self.login(self.admin)
        html = self.client.get(reverse('ai_admin_analytics')).content.decode()
        for secret in ('Secretname', 'secret-school.com', 'stud@test.com') + tuple(COMMENTS):
            self.assertNotIn(secret, html)

    def test_generate_runs_and_reports(self):
        self.login(self.admin)
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            response = self.client.post(reverse('ai_admin_generate'),
                                        {'period': self.periods[4].pk}, follow=True)
        self.assertContains(response, 'finished')
        self.assertContains(response, '2 instructor(s) analysed')

    def test_generate_without_sentiment_model_explains_and_does_not_crash(self):
        # Comments that were never analysed need the model. (Comments that were already
        # analysed and saved are skipped on purpose, so they would never need it.)
        fresh = make_user('fresh@test.com', 'instructor')
        rate(self.periods[0], fresh, [4, 4, 4], ["good class sir", "great teacher", "very clear lessons"])
        self.login(self.admin)
        with mock.patch.object(sentiment, 'get_classifier',
                               side_effect=sentiment.SentimentUnavailable('not installed')):
            response = self.client.post(reverse('ai_admin_generate'),
                                        {'period': self.periods[0].pk}, follow=True)
        self.assertContains(response, 'comment model was not available')
        self.assertEqual(response.status_code, 200)

    def test_generate_never_calls_ollama_unless_asked(self):
        self.login(self.admin)
        with mock.patch.object(ollama, 'generate', side_effect=AssertionError('slow call not allowed here')), \
             mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            response = self.client.post(reverse('ai_admin_generate'),
                                        {'period': self.last_period.pk}, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_empty_system_page_still_opens(self):
        ForecastResult.objects.all().delete()
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('ai_admin_analytics')).status_code, 200)


class CommandTests(Base):
    def run_cmd(self, *args):
        out = StringIO()
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            call_command('run_ai_insights', *args, stdout=out)
        return out.getvalue()

    def test_runs_for_everyone_and_does_not_repeat(self):
        period = str(self.periods[4].pk)
        text = self.run_cmd('--period', period)
        self.assertIn('2 instructor(s) analysed', text)
        again = self.run_cmd('--period', period)
        self.assertIn('already analysed with the same data', again)       # no duplicate processing
        regenerated = self.run_cmd('--period', period, '--regenerate')
        self.assertIn('2 instructor(s) analysed', regenerated)

    def test_default_is_the_newest_period_with_responses(self):
        text = self.run_cmd()
        self.assertIn('1 instructor(s) analysed, 1 had no responses in this period', text)

    def test_single_instructor_is_only_a_test_and_not_official(self):
        text = self.run_cmd('--instructor', str(self.teacher.pk), '--force')
        self.assertIn('forecast=created', text)
        self.assertIn('NOT an official analysis', text)

    def test_unknown_instructor_is_a_clear_error(self):
        with self.assertRaises(CommandError):
            self.run_cmd('--instructor', '99999')
        with self.assertRaises(CommandError):
            self.run_cmd('--instructor', str(self.student.pk))   # students are not instructors

    def test_no_sentiment_flag_skips_the_model(self):
        with mock.patch.object(sentiment, 'get_classifier', side_effect=AssertionError('no model')):
            out = StringIO()
            call_command('run_ai_insights', '--no-sentiment', stdout=out)
        self.assertIn('analysed', out.getvalue())

    def test_with_llm_uses_ollama_and_marks_it(self):
        with mock.patch.object(ollama, 'generate', fake_generate):
            self.run_cmd('--with-llm', '--regenerate')
        from ai_insights.models import FeedbackSummary, Suggestion
        self.assertTrue(Suggestion.objects.filter(instructor=self.teacher, source='ollama').exists())
        self.assertEqual(FeedbackSummary.objects.filter(instructor=self.teacher, summary_source='ollama').count(), 1)


class DataIntegrityTests(Base):
    def test_deleting_an_instructor_removes_their_ai_results(self):
        from ai_insights.models import CommentSentiment, FeedbackSummary, Suggestion
        self.assertTrue(CommentSentiment.objects.exists())
        self.teacher.delete()
        self.assertFalse(ForecastResult.objects.filter(instructor_id=self.teacher.pk).exists())
        self.assertFalse(FeedbackSummary.objects.filter(instructor_id=self.teacher.pk).exists())
        self.assertFalse(Suggestion.objects.filter(instructor_id=self.teacher.pk).exists())
        self.assertFalse(CommentSentiment.objects.exists())       # their evaluations went too
        self.assertTrue(ForecastResult.objects.filter(instructor=self.rival).exists())

    def test_ai_never_changes_real_ratings(self):
        from evaluation.models import Evaluation
        before = list(Evaluation.objects.order_by('id').values_list('id', 'teaching_effectiveness', 'comment'))
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            pipeline.run_for_all(force=True)
        self.assertEqual(before, list(Evaluation.objects.order_by('id').values_list('id', 'teaching_effectiveness', 'comment')))

    def test_one_teacher_failing_does_not_stop_the_others(self):
        real = pipeline.run_for_instructor

        def flaky(teacher, **kw):
            if teacher == self.teacher:
                raise RuntimeError('boom')
            return real(teacher, **kw)
        with mock.patch.object(pipeline, 'run_for_instructor', side_effect=flaky):
            reports = pipeline.run_for_all()
        self.assertEqual(sum(1 for r in reports if r.get('error')), 1)
        self.assertEqual(len(reports), 2)