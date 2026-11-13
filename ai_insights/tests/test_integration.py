# Checks for how the AI results are connected to the existing pages
# (teacher results page, sidebar link, language note).

import sys
from unittest import mock

from django.urls import reverse

from ai_insights.models import ForecastResult
from ai_insights.tests.test_views import Base


class ResultsPageAiTests(Base):

    def results_url(self, period):
        return (reverse('instructor_evaluation_results') +
                f'?tab=ai&school_year={period.school_year}&semester={period.semester}&term={period.term}')

    def test_results_page_never_shows_another_teachers_ai_numbers(self):
        self.login(self.teacher)
        rival = ForecastResult.objects.get(instructor=self.rival, status='ok')
        for period in (self.periods[4], self.periods[4].__class__.objects.order_by('-start_date').first()):
            html = self.client.get(self.results_url(period)).content.decode()
            self.assertNotIn('Zebediah', html)
            self.assertNotIn('Rivalson', html)
            self.assertNotIn(f"{rival.predicted_overall:.2f}", html)

    def test_results_page_shows_comment_summary_for_the_released_period(self):
        self.login(self.teacher)
        latest = self.periods[4].__class__.objects.filter(
            evaluations__teacher=self.teacher).order_by('-start_date').first()
        response = self.client.get(self.results_url(latest))
        self.assertEqual(response.context['state'], 'finished')
        self.assertEqual(response.context['ai']['feedback']['state'], 'ok')
        html = response.content.decode()
        self.assertIn('Feedback summary', html)
        self.assertIn('Suggestions', html)

    def test_no_usage_notes_are_shown_on_the_teacher_panel(self):
        self.login(self.teacher)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertNotIn('Filipino is not one of them', html)
        self.assertNotIn('Student identities and single comments are never shown here', html)


class AdminSidebarTests(Base):

    def test_admin_sees_the_ai_analytics_link(self):
        self.login(self.admin)
        html = self.client.get(reverse('admin_dashboard')).content.decode()
        self.assertIn(reverse('ai_admin_analytics'), html)

    def test_teachers_do_not_get_the_admin_ai_link_on_their_pages(self):
        self.login(self.teacher)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertNotIn(reverse('ai_admin_analytics'), html)
        self.assertNotIn(reverse('ai_admin_generate'), html)


class NoLeakedTemplateCodeTests(Base):
    """A multi-line {# comment #} is shown as plain text by Django. Make sure nothing like that leaks."""

    def test_no_template_code_is_printed_on_the_ai_pages(self):
        self.login(self.teacher)
        pages = [reverse('instructor_dashboard'), reverse('instructor_evaluation_results')]
        for url in pages:
            html = self.client.get(url).content.decode()
            self.assertNotIn('{#', html, url)
            self.assertNotIn('#}', html, url)
            self.assertNotIn('{%', html, url)
        self.login(self.admin)
        html = self.client.get(reverse('ai_admin_analytics')).content.decode()
        for bad in ('{#', '#}', '{%'):
            self.assertNotIn(bad, html)


class MissingLibraryTests(Base):

    def test_generate_explains_when_scikit_learn_is_not_installed(self):
        self.login(self.admin)
        with mock.patch.dict(sys.modules, {'ai_insights.services.pipeline': None}):
            response = self.client.post(reverse('ai_admin_generate'), {'period': self.last_period.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI libraries are not installed')