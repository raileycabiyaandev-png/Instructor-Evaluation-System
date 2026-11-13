# Tests for: specific topics, AI-written summaries (Ollama JSON), checks on AI answers,
# the new admin pages (Student Feedback Analysis, Recommendations) and the new teacher tabs.

import json
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from ai_insights import selectors
from ai_insights.models import AnalysisRun, FeedbackSummary, Suggestion
from ai_insights.services import analysis, ollama, pipeline, sentiment
from ai_insights.services.themes import find_subtopics

from .fake_llm import fake_generate
from .factories import build_history, make_period, make_user, rate, release
from .test_sentiment import fake_classifier
from .test_suggestions_and_ollama import FakeOllamaServer
from .test_views import Base


def topics(*pairs):
    return {s['key']: s for s in find_subtopics(list(pairs))}


class SubtopicTests(SimpleTestCase):
    def test_a_topic_needs_two_comments(self):
        self.assertEqual(find_subtopics([("He is always late", 'negative')]), [])
        found = topics(("He is always late", 'negative'), ("Late again today", 'negative'))
        self.assertEqual(found['late_start']['mentions'], 2)
        self.assertEqual(found['late_start']['tone'], 'problem')
        self.assertEqual(found['late_start']['category'], 'punctuality')

    def test_never_late_is_praise_not_a_problem(self):
        found = topics(("Never late, very punctual", 'positive'), ("He is not late at all", 'positive'))
        self.assertNotIn('late_start', found)
        self.assertEqual(found['on_time']['tone'], 'praise')

    def test_not_clear_is_not_counted_as_clear_praise(self):
        found = topics(("The lesson is not clear and confusing, explains well sometimes", 'negative'),
                       ("Hard to understand the lesson", 'negative'))
        self.assertEqual(found['unclear_explanation']['mentions'], 2)
        self.assertNotIn('explains_well', found)

    def test_tagalog_words_work(self):
        found = topics(("Laging late si sir", 'negative'), ("Nahuhuli siya lagi", 'negative'))
        self.assertIn('late_start', found)


class AnswerCheckTests(SimpleTestCase):
    FACTS = "Positive comments: 3.\nNeutral comments: 1.\nNegative comments: 0.\nUsable comments: 4."
    COMMENTS = ["The teacher explains every lesson with patience and good humour in class",
                "Very kind and helpful when we ask questions"]

    def answer(self, **over):
        data = {'summary': 'Students feel the teacher is patient and kind to them. They like the clear lessons. They hope the class keeps this style.',
                'positive_points': ['Patient explanations'], 'negative_points': []}
        data.update(over)
        return json.dumps(data)

    def run_summary(self, text):
        with mock.patch.object(ollama, 'generate', return_value=text):
            return ollama.summarize_comments(self.COMMENTS, self.FACTS)

    def test_a_good_answer_is_accepted(self):
        out = self.run_summary(self.answer())
        self.assertEqual(out['positive_points'], ['Patient explanations'])

    def test_not_json_is_rejected(self):
        with self.assertRaises(ollama.OllamaUnavailable):
            self.run_summary('Sure! Here is a summary of the comments.')

    def test_json_inside_other_words_is_still_read(self):
        out = self.run_summary('Here you go: ' + self.answer())
        self.assertTrue(out['summary'])

    def test_made_up_numbers_are_rejected(self):
        with self.assertRaises(ollama.OllamaUnavailable):
            self.run_summary(self.answer(summary='Exactly 87 students loved the teacher in this class.'))

    def test_copying_a_comment_is_rejected(self):
        with self.assertRaises(ollama.OllamaUnavailable):
            self.run_summary(self.answer(
                summary='One said: the teacher explains every lesson with patience and good humour in class.'))

    def test_negative_points_are_dropped_when_no_comment_was_negative(self):
        out = self.run_summary(self.answer(negative_points=['Class starts late']))
        self.assertEqual(out['negative_points'], [])

    def test_a_one_sentence_summary_is_rejected(self):
        with self.assertRaises(ollama.OllamaUnavailable):
            self.run_summary(self.answer(summary='Students feel the teacher is patient and kind to them.'))

    def test_the_same_advice_for_two_categories_is_not_accepted(self):
        same = 'Start on time and tell the class early about changes. Check the result next period.'
        data = {'overall': '', 'categories': {'punctuality': same, 'student_engagement': same}}
        with mock.patch.object(ollama, 'generate', return_value=json.dumps(data)):
            out = ollama.recommend("[punctuality] x", ['punctuality', 'student_engagement'])
        self.assertEqual(list(out['categories']), ['punctuality'])

    def test_short_or_wrong_shape_is_rejected(self):
        for bad in (self.answer(summary='ok'), self.answer(positive_points='a string'), '[1, 2]'):
            with self.assertRaises(ollama.OllamaUnavailable):
                self.run_summary(bad)

    def test_scrub_removes_contact_details(self):
        out = ollama.scrub("mail a.b@c.com, call 0917 123 4567, see https://x.io/p and @teacher")
        for gone in ('a.b@c.com', '4567', 'https', '@teacher'):
            self.assertNotIn(gone, out)

    def test_recommendation_for_one_category_must_not_talk_about_another(self):
        facts = "[punctuality] Punctuality: score 3.0."
        data = {'overall': '', 'categories': {
            'punctuality': 'Start on time and tell the class early about changes.',
            'student_engagement': 'Your Punctuality is the real problem, fix that first please.'}}
        with mock.patch.object(ollama, 'generate', return_value=json.dumps(data)):
            out = ollama.recommend(facts, ['punctuality', 'student_engagement'])
        self.assertEqual(list(out['categories']), ['punctuality'])

    def test_status_chip(self):
        with override_settings(AI_OLLAMA_URL='http://127.0.0.1:9'):
            self.assertEqual(ollama.status()['state'], 'down')
        with override_settings(AI_OLLAMA_ENABLED=False):
            self.assertEqual(ollama.status()['state'], 'off')

    def test_summary_over_real_http_asks_for_json(self):
        answer = json.dumps({'summary': 'Students feel the teacher is patient and kind to them. They like the clear lessons. They hope the class keeps this style.',
                             'positive_points': ['Patient'], 'negative_points': []})
        with FakeOllamaServer(answer) as srv, override_settings(AI_OLLAMA_URL=srv.url):
            out = ollama.summarize_comments(self.COMMENTS, self.FACTS)
        path, payload = srv.requests[0]
        self.assertEqual((path, payload['format']), ('/api/generate', 'json'))
        self.assertIn('Anonymous student comments', payload['prompt'])
        self.assertEqual(out['positive_points'], ['Patient'])

    def test_status_ready_and_missing_model(self):
        body = json.dumps({'models': [{'name': 'llama3.2:latest'}]}).encode()

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return body
        with mock.patch('urllib.request.urlopen', return_value=R()):
            with override_settings(AI_OLLAMA_MODEL='llama3.2'):
                self.assertEqual(ollama.status()['state'], 'ready')
            with override_settings(AI_OLLAMA_MODEL='mistral'):
                self.assertEqual(ollama.status()['state'], 'no_model')


class AiFeedbackBase(Base):
    """Alice has the Base data. Add comments that name real topics, then run with the fake AI."""

    def setUp(self):
        super().setUp()
        rate(make_period(6), self.teacher, [3, 3, 4, 4],
             ["Always late to class", "Late again and the class is boring",
              "Great teacher, explains well and easy to understand", "Thank you sir, very good, explains well"])
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()), \
                mock.patch.object(ollama, 'generate', fake_generate):
            pipeline.run_for_all(use_llm=True, force=True)
        self.release_run = release(self.admin, period=self.release_run.period.__class__.objects.get(
            pk=Suggestion.objects.filter(instructor=self.teacher).first().period_id))


class PipelineAiTests(AiFeedbackBase):
    def test_summary_is_ai_written_and_labelled(self):
        row = FeedbackSummary.objects.get(instructor=self.teacher, hidden=False,
                                          period=Suggestion.objects.filter(instructor=self.teacher).first().period)
        self.assertEqual(row.summary_source, 'ollama')
        self.assertTrue(row.llm_model)
        self.assertIn('friendly', row.summary_text)
        self.assertEqual(row.positive_points, ['Friendly manner in class'])

    def test_topics_are_saved_from_the_real_comments(self):
        row = FeedbackSummary.objects.filter(instructor=self.teacher, hidden=False).order_by('-period__start_date').first()
        keys = {s['key'] for s in row.subthemes}
        self.assertIn('late_start', keys)
        self.assertIn('explains_well', keys)

    def test_rule_suggestions_use_those_topics(self):
        newest = Suggestion.objects.filter(instructor=self.teacher).order_by('-period__start_date').first().period
        punctuality = Suggestion.objects.get(instructor=self.teacher, source='rule', category='punctuality',
                                             period=newest)
        topics_here = [t['key'] for t in punctuality.evidence['topics']]
        self.assertIn('late_start', topics_here)

    def test_falls_back_to_rule_text_when_the_ai_fails(self):
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()), \
                mock.patch.object(ollama, 'generate', side_effect=ollama.OllamaUnavailable('down')):
            pipeline.run_for_all(use_llm=True, force=True)
        row = FeedbackSummary.objects.filter(instructor=self.teacher, hidden=False).order_by('-period__start_date').first()
        self.assertEqual(row.summary_source, 'rule')
        self.assertIn('usable comments', row.summary_text)
        self.assertFalse(Suggestion.objects.filter(source='ollama').exists())

    def test_run_message_says_how_many_got_ai_text(self):
        period = Suggestion.objects.filter(instructor=self.teacher).first().period
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()), \
                mock.patch.object(ollama, 'generate', fake_generate):
            run, _ = analysis.start_run(period, user=self.admin, regenerate=True, use_llm=True)
            analysis.launch(run, background=False)
        run.refresh_from_db()
        self.assertIn('Ollama wrote the texts for', run.message)


class TeacherPanelTests(AiFeedbackBase):
    def html(self, section='feedback'):
        self.login(self.teacher)
        return self.client.get(reverse('instructor_dashboard'), {'section': section}).content.decode()

    def test_tabs_and_ai_labels(self):
        html = self.html()
        for text in ('Feedback', 'Topics to improve', 'Suggestions', 'Written by AI', 'What students liked',
                     'What students want improved', 'Friendly manner in class'):
            self.assertIn(text, html)

    def test_each_category_has_its_own_card_with_its_own_step(self):
        html = self.html('suggestions')
        self.assertIn('Class starts or ends late', html)
        self.assertIn('Start and end on the scheduled minute', html)

    def test_no_usage_notes(self):
        html = self.html()
        for text in ('Sentiment by a computer model', 'never shown here', 'Written by an AI from your scores only'):
            self.assertNotIn(text, html)

    def test_comments_are_never_shown(self):
        html = self.html()
        self.assertNotIn('Always late to class', html)
        self.assertNotIn('Secretname', html)


class AdminPagesTests(AiFeedbackBase):
    def get(self, name, user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse(name), params)

    def test_only_admin_can_open(self):
        for name in ('ai_admin_feedback', 'ai_admin_recommendations'):
            self.assertEqual(self.get(name, self.teacher).status_code, 403)
            self.assertEqual(self.get(name, self.student).status_code, 403)
            self.assertEqual(self.get(name).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ai_admin_feedback')).status_code, 302)

    def test_feedback_page_shows_summary_without_any_comment_or_student(self):
        response = self.get('ai_admin_feedback')
        html = response.content.decode()
        self.assertIn('AI Feedbacks', html)
        self.assertIn('AI summary', html)
        self.assertNotIn('Always late to class', html)
        self.assertNotIn('Secretname', html)
        self.assertNotIn('secret-school.com', html)
        self.assertGreaterEqual(response.context['data']['stats']['ai_written'], 1)

    def test_feedback_filters_are_checked(self):
        data = self.get('ai_admin_feedback', tone='<script>', source='bogus').context['data']
        self.assertEqual((data['tone'], data['source']), ('', ''))
        data = self.get('ai_admin_feedback', source='rule').context['data']
        self.assertEqual(data['stats']['instructors'], 0)
        data = self.get('ai_admin_feedback', source='ollama').context['data']
        self.assertGreaterEqual(data['stats']['instructors'], 1)

    def test_feedback_department_filter_hides_other_departments(self):
        html = self.get('ai_admin_feedback', department=self.ge.pk).content.decode()
        self.assertNotIn('Always late', html)
        data = self.get('ai_admin_feedback', department=self.ge.pk).context['data']
        self.assertEqual(data['stats']['comments'], 0)

    def test_recommendations_page_lists_cards_with_ai_text(self):
        response = self.get('ai_admin_recommendations')
        data = response.context['data']
        self.assertGreaterEqual(data['stats']['ai_written'], 5)
        self.assertEqual(data['stats']['total'], sum(data['stats'][k] for k in ('high', 'medium', 'low', 'strength')))
        self.assertContains(response, 'Recommendations')
        self.assertContains(response, 'with small steps. Check the result next period.')

    def test_recommendation_filters(self):
        for params, check in (
            ({'category': 'punctuality'}, lambda c: c['key'] == 'punctuality'),
            ({'priority': 'high'}, lambda c: c['priority'] == 'high'),
            ({'source': 'rule'}, lambda c: not c['ai']),
        ):
            cards = list(self.get('ai_admin_recommendations', **params).context['data']['pager'])
            self.assertTrue(all(check(c) for c in cards), params)
        data = self.get('ai_admin_recommendations', category='nonsense', priority='x').context['data']
        self.assertEqual((data['category'], data['priority']), ('', ''))

    def test_recommendations_are_paginated_and_keep_filters_in_links(self):
        for i in range(4):
            t = make_user(f'extra{i}@test.com', 'instructor', department=self.it)
            build_history(t, [3.0, 3.25, 3.5, 3.75, 4.0])
        pipeline.run_for_all(use_sentiment=False)
        response = self.get('ai_admin_recommendations', department=self.it.pk)
        pager = response.context['data']['pager']
        self.assertGreater(pager.paginator.num_pages, 1)
        self.assertContains(response, f'department={self.it.pk}&amp;page=2')
        self.assertEqual(self.get('ai_admin_recommendations', page=999).status_code, 200)
        self.assertEqual(self.get('ai_admin_recommendations', page='abc').status_code, 200)

    def test_generate_panel_has_ai_option_and_no_usage_notes(self):
        with mock.patch.object(ollama, 'status', return_value={'state': 'ready', 'model': 'llama3.2', 'text': 'Ollama ready (llama3.2)'}):
            html = self.get('ai_admin_feedback').content.decode()
        self.assertIn('Ollama ready (llama3.2)', html)
        self.assertRegex(html, r'name="use_llm"\s+checked')
        with mock.patch.object(ollama, 'status', return_value={'state': 'down', 'model': 'llama3.2', 'text': 'Ollama is not running'}):
            html = self.get('ai_admin_feedback').content.decode()
        self.assertIn('Ollama is not running', html)
        self.assertNotRegex(html, r'name="use_llm"\s+checked')
        for note in ('Predictions are never guarantees', 'Only an administrator can generate it'):
            self.assertNotIn(note, html)

    def test_existing_admin_pages_lost_their_notes(self):
        for name in ('ai_admin_analytics', 'ai_admin_forecasting'):
            html = self.get(name).content.decode()
            for note in ('Predictions continue past patterns', 'ai-limits', 'Predictions are never guarantees'):
                self.assertNotIn(note, html)

    def test_generate_can_go_back_to_the_new_pages_only(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('ai_admin_generate'), {'next': 'ai_admin_recommendations'})
        self.assertRedirects(response, reverse('ai_admin_recommendations'), fetch_redirect_response=False)
        response = self.client.post(reverse('ai_admin_generate'), {'next': 'https://evil.example'})
        self.assertRedirects(response, reverse('ai_admin_analytics'), fetch_redirect_response=False)