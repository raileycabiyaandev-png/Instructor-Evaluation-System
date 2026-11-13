"""Alert severity (Low / Medium / High), the department view, reviewed button."""
from types import SimpleNamespace as NS

from django.urls import reverse

from ai_insights.models import Alert, AlertSettings
from ai_insights.services import alerts

from .test_admin_forecasting import ForecastPageBase


def series(*overalls):
    return [{'label': f"P{i}", 'overall': v, 'responses': 10, 'period': None,
             'categories': {k: v for k in ('teaching_effectiveness', 'communication_skills', 'classroom_management',
                                           'punctuality', 'student_engagement')}} for i, v in enumerate(overalls)]


def forecast(predicted):
    return NS(status='ok', predicted_overall=predicted, range_low=predicted - .2, range_high=predicted + .2,
              target_label='Next period')


class SeverityRuleTests(ForecastPageBase):
    def setUp(self):
        super().setUp()
        self.s = AlertSettings.get()

    def test_forecast_alone_is_low(self):
        found = alerts.evaluate(series(4.0, 4.0, 4.0, 4.0), forecast(3.4), None, self.s)
        self.assertEqual(found['forecast_low']['severity'], 'low')

    def test_very_low_forecast_is_medium(self):
        found = alerts.evaluate(series(4.0, 4.0, 4.0, 4.0), forecast(2.8), None, self.s)
        self.assertEqual(found['forecast_low']['severity'], 'medium')

    def test_forecast_drop_is_low_and_never_high(self):
        found = alerts.evaluate(series(4.8, 4.8, 4.8, 4.8), forecast(4.3), None, self.s)
        self.assertEqual(found['forecast_drop']['severity'], 'low')
        self.assertNotIn('high', {v['severity'] for k, v in found.items() if k.startswith('forecast')})

    def test_big_real_drop_is_high_small_is_medium(self):
        self.assertEqual(alerts.evaluate(series(4.5, 4.1), None, None, self.s)['score_drop']['severity'], 'medium')
        self.assertEqual(alerts.evaluate(series(4.8, 4.1), None, None, self.s)['score_drop']['severity'], 'high')


class AlertPageTests(ForecastPageBase):
    def login(self, user):
        self.client.force_login(user)

    def get(self, qs=''):
        self.login(self.admin)
        return self.client.get(reverse('ai_admin_alerts') + qs)

    def test_attention_groups_by_department_with_numbers(self):
        data = self.get().context['data']
        names = {d['department'] for d in data['attention']}
        self.assertIn(self.ge.name, names)
        row = next(r for d in data['attention'] for r in d['rows'] if r['teacher'] == self.rival)
        self.assertGreater(row['alerts'], 0)
        self.assertGreater(row['evaluations'], 0)
        self.assertIsNotNone(row['average'])

    def test_low_severity_filter_is_valid(self):
        self.assertEqual(self.get('?severity=low').status_code, 200)

    def test_alert_shows_supporting_data_and_reviewed_button(self):
        r = self.get()
        self.assertContains(r, 'Supporting data')
        self.assertContains(r, 'Mark as reviewed')
        self.assertTrue(r.context['data']['history'])

    def test_quick_review_button_marks_reviewed(self):
        a = Alert.objects.filter(instructor=self.rival, status='new').first()
        self.login(self.admin)
        self.client.post(reverse('ai_admin_alert_update', args=[a.pk]), {'status': 'reviewed', 'note': '', 'qs': ''})
        a.refresh_from_db()
        self.assertEqual(a.status, 'reviewed')
        self.assertEqual(a.reviewed_by, self.admin)

    def test_year_semester_filters_work(self):
        p = Alert.objects.first().period
        r = self.get(f'?school_year={p.school_year}&semester={p.semester}')
        self.assertGreater(r.context['data']['total'], 0)