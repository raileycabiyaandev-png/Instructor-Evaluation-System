"""AI Overview (admin): status, school numbers, school text, privacy, access."""
import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from ai_insights import selectors
from ai_insights.filters import parse_filters
from ai_insights.models import AnalysisRun, SchoolInsight
from ai_insights.services import ollama, school

from .test_views import Base, COMMENTS


class OverviewTests(Base):
    def get(self, qs=''):
        self.login(self.admin)
        return self.client.get(reverse('ai_admin_analytics') + qs)

    def test_page_opens_for_admin_only(self):
        self.assertEqual(self.get().status_code, 200)
        for user in (self.teacher, self.student):
            self.login(user)
            self.assertEqual(self.client.get(reverse('ai_admin_analytics')).status_code, 403)

    def test_status_completed_and_school_numbers(self):
        r = self.get()
        d = r.context['data']
        self.assertEqual(d['period'], self.last_period)
        self.assertEqual(d['state']['key'], 'completed')
        self.assertIsNotNone(d['school']['average'])
        self.assertIn('Completed', r.content.decode())

    def test_status_not_generated_when_no_run(self):
        AnalysisRun.objects.all().delete()
        self.assertEqual(self.get().context['data']['state']['key'], 'not_generated')

    def test_status_processing_and_failed(self):
        AnalysisRun.objects.filter(period=self.last_period).update(status='running')
        self.assertEqual(self.get().context['data']['state']['key'], 'processing')
        AnalysisRun.objects.filter(period=self.last_period).update(status='failed')
        self.assertEqual(self.get().context['data']['state']['key'], 'failed')

    def test_period_filter_changes_the_period(self):
        first = self.periods[1]
        d = self.get(f'?period={first.pk}').context['data']
        self.assertEqual(d['period'], first)

    def test_forecast_never_invented_with_short_history(self):
        d = self.get(f'?period={self.periods[1].pk}').context['data']
        self.assertFalse(d['forecast']['available'])
        self.assertIsNone(d['forecast'].get('average'))

    def test_school_facts_hold_no_names_or_comments(self):
        f = school.facts_for(self.last_period)
        text = school.facts_text(f) + json.dumps(f, default=str)
        for secret in COMMENTS + ['Rivalson', 'alice@test.com', 'rival@test.com']:
            self.assertNotIn(secret, text)

    def test_generate_saves_rule_text_without_ollama(self):
        ins = school.generate(self.last_period, use_llm=False)
        self.assertEqual(ins.summary_source, 'rule')
        self.assertTrue(ins.summary_text)
        self.assertEqual(SchoolInsight.objects.filter(period=self.last_period).count(), 1)
        school.generate(self.last_period)
        self.assertEqual(SchoolInsight.objects.filter(period=self.last_period).count(), 1)

    def test_ollama_text_used_when_it_passes_checks(self):
        f = school.facts_for(self.last_period)
        good = {'summary': f"The school average is {f['average']:.2f} out of 5 in this period.",
                'strengths': [], 'weaknesses': [], 'suggestions': []}
        with mock.patch.object(ollama, 'school_overview', return_value=good):
            ins = school.generate(self.last_period, use_llm=True)
        self.assertEqual(ins.summary_source, 'ollama')

    def test_ollama_down_falls_back_to_rules(self):
        with mock.patch.object(ollama, 'school_overview', side_effect=ollama.OllamaUnavailable('off')):
            ins = school.generate(self.last_period, use_llm=True)
        self.assertEqual(ins.summary_source, 'rule')

    def test_ollama_invented_number_is_rejected(self):
        bad = json.dumps({'summary': 'The school average is 4.99 out of 5 in this period, a great result.',
                          'strengths': [], 'weaknesses': [], 'suggestions': []})
        with mock.patch.object(ollama, 'generate', return_value=bad):
            with self.assertRaises(ollama.OllamaUnavailable):
                ollama.school_overview(school.facts_text(school.facts_for(self.last_period)))

    def test_summary_shown_on_page_without_student_data(self):
        school.generate(self.last_period)
        html = self.get().content.decode()
        self.assertIn('Rule-based', html)
        for secret in COMMENTS + ['Rivalson', 'stud@test.com']:
            self.assertNotIn(secret, html)

    def test_no_data_page_renders(self):
        from evaluation.models import Evaluation
        Evaluation.objects.all().delete()
        r = self.get()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['data']['period'])