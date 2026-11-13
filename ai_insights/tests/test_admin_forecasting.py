# Stage 1 tests: Performance Forecasting page (permissions, filters, saved results, backtest).

from django.test import TestCase
from django.urls import reverse

from academics.models import Department
from ai_insights.filters import parse_filters
from ai_insights.models import ForecastResult
from ai_insights.services import forecasting, pipeline
from ai_insights.services.data import instructor_series

from .factories import build_history, make_user, release

URL = 'ai_admin_forecasting'


class ForecastPageBase(TestCase):
    def setUp(self):
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.ge, _ = Department.objects.get_or_create(code='GENED', defaults={'name': 'General Education'})
        self.alice = make_user('alice@test.com', 'instructor', department=self.it)
        self.rival = make_user('rival@test.com', 'instructor', department=self.ge)
        self.rival.first_name, self.rival.last_name = 'Zebediah', 'Rivalson'
        self.rival.save()
        self.newbie = make_user('newbie@test.com', 'instructor', department=self.it)
        self.newbie.first_name, self.newbie.last_name = 'Nina', 'Newbie'
        self.newbie.save()
        self.admin = make_user('admin@test.com', 'admin')
        self.student = make_user('stud@test.com', 'student')

        self.alice_periods = build_history(self.alice, [3.0, 3.25, 3.5, 3.75, 4.0])
        build_history(self.rival, [4.0, 3.5, 3.0, 2.5, 2.0])
        build_history(self.newbie, [4.0, 4.0])               # only 2 periods: too little
        pipeline.run_for_all(use_sentiment=False)
        release(self.admin)

    def get(self, user=None, **params):
        if user:
            self.client.force_login(user)
        return self.client.get(reverse(URL), params)


class PermissionTests(ForecastPageBase):
    def test_visitor_is_sent_to_login(self):
        response = self.get()
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_teacher_and_student_are_refused(self):
        self.assertEqual(self.get(self.alice).status_code, 403)
        self.assertEqual(self.get(self.student).status_code, 403)

    def test_admin_can_open(self):
        self.assertEqual(self.get(self.admin).status_code, 200)

    def test_teacher_cannot_press_generate(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(reverse('ai_admin_generate')).status_code, 403)


class SavedResultsTests(ForecastPageBase):
    def test_numbers_on_the_page_come_from_saved_rows(self):
        row = ForecastResult.objects.get(instructor=self.alice, status='ok')
        response = self.get(self.admin)
        shown = {r['teacher'].pk: r for r in response.context['data']['pager']}
        self.assertEqual(shown[self.alice.pk]['predicted'], row.predicted_overall)
        self.assertContains(response, f"{row.predicted_overall:.2f}")

    def test_too_little_history_shows_message_and_no_number(self):
        response = self.get(self.admin)
        shown = {r['teacher'].pk: r for r in response.context['data']['pager']}
        self.assertEqual(shown[self.newbie.pk]['state'], 'insufficient')
        self.assertNotIn('predicted', shown[self.newbie.pk])
        self.assertContains(response, 'Not enough data')

    def test_forecast_saves_model_version_and_backtest(self):
        row = ForecastResult.objects.get(instructor=self.alice, status='ok')
        self.assertEqual(row.model_version, forecasting.MODEL_VERSION)
        self.assertEqual(len(row.backtest), row.validation_steps)
        self.assertGreaterEqual(row.validation_steps, 1)
        for step in row.backtest:
            self.assertTrue(1 <= step['predicted'] <= 5)
            self.assertTrue(1 <= step['actual'] <= 5)

    def test_backtest_never_uses_the_future(self):
        # Change ONLY the newest real score. Every earlier test prediction must stay the same.
        series = instructor_series(self.alice)
        y = [s['overall'] for s in series]
        terms = [1] * len(y)
        before = forecasting.walk_forward_predictions('trend', y, terms)
        changed = y[:-1] + [1.0]
        after = forecasting.walk_forward_predictions('trend', changed, terms)
        self.assertEqual(before[:-1], after[:-1])

    def test_page_reading_does_not_run_a_model(self):
        from unittest import mock
        with mock.patch.object(forecasting, 'make_forecast', side_effect=AssertionError('model ran')):
            self.assertEqual(self.get(self.admin).status_code, 200)

    def test_no_student_names_or_emails_on_the_page(self):
        response = self.get(self.admin)
        self.assertNotContains(response, 'secret-school.com')
        self.assertNotContains(response, 'Secretname')
        self.assertNotContains(response, 'stud@test.com')

    def test_empty_database_still_opens(self):
        ForecastResult.objects.all().delete()
        response = self.get(self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No forecast yet')


class FilterTests(ForecastPageBase):
    def names(self, response):
        return {r['teacher'].pk for r in response.context['data']['pager']}

    def test_department_filter(self):
        response = self.get(self.admin, department=self.ge.pk)
        self.assertEqual(self.names(response), {self.rival.pk})

    def test_instructor_filter_shows_detail_chart(self):
        response = self.get(self.admin, instructor=self.alice.pk)
        self.assertEqual(self.names(response), {self.alice.pk})
        detail = response.context['data']['detail']
        self.assertEqual(len(detail['chart']['labels']), 6)           # 5 real periods + 1 predicted
        self.assertIsNone(detail['chart']['actual'][-1])               # prediction is never drawn as actual
        self.assertIsNotNone(detail['chart']['next'][-1])
        self.assertTrue(detail['has_backtest'])

    def test_instructor_without_forecast_gets_clear_message(self):
        response = self.get(self.admin, instructor=self.newbie.pk)
        self.assertIsNone(response.context['data']['detail']['chart'])
        self.assertIn(response.context['data']['detail']['row']['state'], ('insufficient', 'baseline', 'none'))

    def test_period_filter_only_keeps_forecasts_based_on_that_period(self):
        last = self.alice_periods[-1]
        response = self.get(self.admin, period=last.pk)
        self.assertIn(self.alice.pk, self.names(response))
        shown = {r['teacher'].pk: r for r in response.context['data']['pager']}
        self.assertEqual(shown[self.alice.pk]['state'], 'ok')
        response = self.get(self.admin, period=self.alice_periods[0].pk)    # no forecast was made from it
        shown = {r['teacher'].pk: r for r in response.context['data']['pager']}
        self.assertEqual(shown[self.alice.pk]['state'], 'none')

    def test_term_filter(self):
        response = self.get(self.admin, term='2024-2025|1st')
        shown = {r['teacher'].pk: r for r in response.context['data']['pager']}
        # all five periods of alice end in 2nd semester at index 4, so no 1st-semester-based forecast
        self.assertEqual(shown[self.alice.pk]['state'], 'none')
        response = self.get(self.admin, term='2024-2025|2nd')
        shown = {r['teacher'].pk: r for r in response.context['data']['pager']}
        self.assertEqual(shown[self.alice.pk]['state'], 'ok')

    def test_wrong_filter_values_are_ignored_not_crashing(self):
        response = self.get(self.admin, department='999', instructor='abc', period='123456', term="x|y'; DROP", page='zzz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.names(response)), 3)

    def test_a_student_or_admin_id_cannot_be_chosen_as_instructor(self):
        filters = parse_filters({'instructor': str(self.student.pk)})
        self.assertIsNone(filters.instructor)
        filters = parse_filters({'instructor': str(self.admin.pk)})
        self.assertIsNone(filters.instructor)

    def test_instructor_outside_chosen_department_is_ignored(self):
        filters = parse_filters({'department': str(self.ge.pk), 'instructor': str(self.alice.pk)})
        self.assertIsNone(filters.instructor)

    def test_period_outside_chosen_term_is_ignored(self):
        filters = parse_filters({'term': '2024-2025|1st', 'period': str(self.alice_periods[-1].pk)})
        self.assertIsNone(filters.period)

    def test_inactive_instructor_is_not_listed(self):
        self.rival.is_active = False
        self.rival.save()
        response = self.get(self.admin)
        self.assertNotIn(self.rival.pk, self.names(response))


class GenerateRedirectTests(ForecastPageBase):
    def test_generate_goes_back_to_forecasting_page(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('ai_admin_generate'), {'next': 'ai_admin_forecasting'})
        self.assertRedirects(response, reverse('ai_admin_forecasting'), fetch_redirect_response=False)

    def test_generate_ignores_an_outside_address(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('ai_admin_generate'), {'next': 'https://evil.example/'})
        self.assertRedirects(response, reverse('ai_admin_analytics'), fetch_redirect_response=False)

    def test_running_twice_does_not_make_duplicate_forecasts(self):
        before = ForecastResult.objects.count()
        pipeline.run_for_all(use_sentiment=False)
        pipeline.run_for_all(use_sentiment=False, force=True)
        self.assertEqual(ForecastResult.objects.count(), before)