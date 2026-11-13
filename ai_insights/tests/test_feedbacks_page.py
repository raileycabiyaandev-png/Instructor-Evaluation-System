"""AI Feedbacks page (admin): counts, anonymous real comments, privacy."""
from django.urls import reverse

from ai_insights.models import FeedbackSummary

from .factories import make_user, rate
from .test_views import Base, COMMENTS


class FeedbacksPageTests(Base):
    def get(self, qs=''):
        self.login(self.admin)
        return self.client.get(reverse('ai_admin_feedback') + qs)

    def test_admin_only(self):
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('ai_admin_feedback')).status_code, 403)

    def test_written_and_analysed_counts(self):
        s = self.get().context['data']['stats']
        self.assertEqual(s['written'], 4)
        self.assertEqual(s['comments'], 4)

    def test_no_raw_comments_without_an_instructor(self):
        html = self.get().content.decode()
        for text in COMMENTS:
            self.assertNotIn(text, html)

    def test_instructor_filter_shows_anonymous_comments(self):
        r = self.get(f'?instructor={self.teacher.pk}')
        raw = r.context['data']['raw']
        self.assertEqual(raw['state'], 'ok')
        self.assertEqual(sorted(raw['comments']), sorted(COMMENTS))
        self.assertContains(r, 'Student comments')
        self.assertNotContains(r, 'stud@test.com')

    def test_contact_details_are_removed(self):
        FeedbackSummary.objects.filter(instructor=self.teacher).update(hidden=False)
        rate(self.last_period, self.teacher, [4], ["call me 09171234567 or mail x@y.com @handle http://a.b/c"])
        raw = self.get(f'?instructor={self.teacher.pk}').context['data']['raw']['comments']
        joined = ' '.join(raw)
        for bad in ('09171234567', 'x@y.com', '@handle', 'http'):
            self.assertNotIn(bad, joined)

    def test_hidden_summary_hides_comments(self):
        FeedbackSummary.objects.filter(instructor=self.teacher).update(hidden=True, hidden_reason='Too few comments.')
        raw = self.get(f'?instructor={self.teacher.pk}').context['data']['raw']
        self.assertEqual(raw['state'], 'hidden')
        self.assertEqual(raw['comments'], [])

    def test_suggestions_tab_has_data(self):
        self.assertTrue(self.get().context['data']['suggestions'] is not None)