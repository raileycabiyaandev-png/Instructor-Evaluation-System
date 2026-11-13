import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from ai_insights.models import Suggestion
from ai_insights.services import ollama, pipeline, sentiment
from ai_insights.services.suggestions import FALLBACK_ACTION, build_rule_suggestions

from .factories import build_history, make_period, make_user, rate
from .fake_llm import fake_generate
from .test_sentiment import fake_classifier

FIELDS = ['teaching_effectiveness', 'communication_skills', 'classroom_management',
          'punctuality', 'student_engagement']


def row(scores, responses=10, label='Finals · S.Y. 2024-2025'):
    cats = dict(zip(FIELDS, scores))
    return {'categories': cats, 'overall': sum(scores) / 5, 'responses': responses, 'label': label}


def by_cat(items):
    return {i['category']: i for i in items}


class RuleTests(SimpleTestCase):
    def test_all_five_categories_always_get_a_suggestion(self):
        items = build_rule_suggestions(row([4.0, 4.0, 4.0, 4.0, 4.0]))
        self.assertEqual({i['category'] for i in items}, set(FIELDS))
        self.assertTrue(all(i['source'] == 'rule' for i in items))

    def test_thresholds(self):
        items = by_cat(build_rule_suggestions(row([3.2, 4.0, 4.7, 4.0, 4.0])))
        self.assertEqual(items['teaching_effectiveness']['priority'], 'high')          # below 3.5
        self.assertEqual(items['classroom_management']['priority'], 'strength')        # 4.5 or more
        self.assertEqual(items['communication_skills']['priority'], 'low')

    def test_lowest_category_in_the_good_range_becomes_medium(self):
        items = by_cat(build_rule_suggestions(row([4.0, 3.9, 4.0, 4.0, 4.0])))
        self.assertEqual(items['communication_skills']['priority'], 'medium')
        self.assertIn('lowest', items['communication_skills']['evidence']['reasons'][0])

    def test_a_drop_since_last_period_is_used_and_shown_as_evidence(self):
        items = by_cat(build_rule_suggestions(row([4.0, 4.0, 4.0, 3.8, 4.0]), previous=row([4.0] * 5)))
        e = items['punctuality']['evidence']
        self.assertEqual(items['punctuality']['priority'], 'medium')
        self.assertEqual((e['score'], e['previous_score'], e['change']), (3.8, 4.0, -0.2))

    def test_negative_comments_and_forecast_drop_are_used(self):
        theme = [{'key': 'student_engagement', 'label': 'x', 'mentions': 4, 'positive': 0, 'negative': 3}]
        items = by_cat(build_rule_suggestions(row([4.1, 4.1, 4.1, 4.1, 4.1]), themes=theme))
        self.assertEqual(items['student_engagement']['priority'], 'medium')
        forecast = {'status': 'ok', 'predicted_categories': {'punctuality': {'value': 3.8}}}
        items = by_cat(build_rule_suggestions(row([4.1, 4.1, 4.1, 4.1, 4.1]), forecast=forecast))
        self.assertEqual(items['punctuality']['evidence']['predicted_change'], -0.3)

    def test_every_category_has_its_own_fallback_step(self):
        self.assertEqual(set(FALLBACK_ACTION), set(FIELDS))
        self.assertEqual(len(set(FALLBACK_ACTION.values())), 5)

    def test_numbers_in_the_text_come_only_from_the_evidence(self):
        import re
        for item in build_rule_suggestions(row([3.0, 4.0, 4.8, 4.0, 3.4]), previous=row([3.2, 4.0, 4.8, 4.0, 3.4])):
            e = item['evidence']
            allowed = {str(x) for x in (e['score'], e['gap_to_best'], abs(e['change'] or 0))} | {'5', '0'}
            allowed |= {f"{float(x):g}" for x in list(allowed) if re.match(r'^\d+(\.\d+)?$', x)}
            for number in re.findall(r'\d+(?:\.\d+)?', item['text']):
                self.assertIn(number, allowed | {f"{float(n):g}" for n in allowed})

    def test_steps_come_from_the_topics_students_really_wrote_about(self):
        subs = [{'key': 'late_start', 'label': 'Class starts or ends late', 'category': 'punctuality',
                 'tone': 'problem', 'mentions': 3, 'positive': 0, 'negative': 3}]
        items = by_cat(build_rule_suggestions(row([4.0, 4.0, 4.0, 3.9, 4.0]), subthemes=subs))
        p = items['punctuality']
        self.assertEqual(p['priority'], 'medium')
        self.assertIn('3 comments', p['text'])
        self.assertEqual([a['topic'] for a in p['evidence']['actions']], ['Class starts or ends late'])
        self.assertIn('scheduled minute', p['evidence']['actions'][0]['text'])
        # other categories did NOT get this step
        self.assertTrue(all(not i['evidence']['actions'] or i['category'] == 'punctuality'
                            for k, i in items.items() if i['priority'] != 'high'))

    def test_two_instructors_with_different_comments_get_different_text(self):
        late = [{'key': 'late_start', 'label': 'Class starts or ends late', 'category': 'punctuality',
                 'tone': 'problem', 'mentions': 3, 'positive': 0, 'negative': 3}]
        noisy = [{'key': 'noisy', 'label': 'Noisy or out of control class', 'category': 'classroom_management',
                  'tone': 'problem', 'mentions': 4, 'positive': 0, 'negative': 4}]
        a = by_cat(build_rule_suggestions(row([4.0, 4.0, 4.0, 3.9, 4.0]), subthemes=late))
        b = by_cat(build_rule_suggestions(row([4.0, 4.0, 3.9, 4.0, 4.0]), subthemes=noisy))
        self.assertNotEqual(a['punctuality']['text'], b['classroom_management']['text'])
        self.assertEqual(b['punctuality']['evidence']['actions'], [])

    def test_no_topic_found_says_so_and_does_not_invent_a_problem(self):
        item = by_cat(build_rule_suggestions(row([3.0, 4.0, 4.0, 4.0, 4.0])))['teaching_effectiveness']
        self.assertIn('No clear topic', item['text'])
        self.assertEqual(item['evidence']['actions'][0]['topic'], 'Find the reason')

    def test_low_score_but_praising_comments_is_reported_as_a_mismatch(self):
        praise = [{'key': 'explains_well', 'label': 'Explains lessons well', 'category': 'teaching_effectiveness',
                   'tone': 'praise', 'mentions': 4, 'positive': 4, 'negative': 0}]
        item = by_cat(build_rule_suggestions(row([3.0, 4.0, 4.0, 4.0, 4.0]), subthemes=praise))['teaching_effectiveness']
        self.assertIn('do not match', item['text'])

    def test_strength_names_what_students_praised(self):
        praise = [{'key': 'on_time', 'label': 'Comes on time', 'category': 'punctuality',
                   'tone': 'praise', 'mentions': 3, 'positive': 3, 'negative': 0}]
        item = by_cat(build_rule_suggestions(row([4.0, 4.0, 4.0, 4.8, 4.0]), subthemes=praise))['punctuality']
        self.assertEqual(item['priority'], 'strength')
        self.assertIn('comes on time', item['text'])


class FakeOllamaServer:
    """A tiny local web server that talks like Ollama, to test the REAL client code."""

    def __init__(self, answer='Your score was 4.00 out of 5.', status=200, delay=0):
        outer = self
        outer.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers['Content-Length'])
                outer.requests.append((self.path, json.loads(self.rfile.read(length))))
                body = json.dumps({'response': answer}).encode() if status == 200 else b'not json'
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


class OllamaClientTests(SimpleTestCase):
    def test_request_format_and_answer(self):
        with FakeOllamaServer('Hello 4.00') as srv, override_settings(AI_OLLAMA_URL=srv.url, AI_OLLAMA_MODEL='m1'):
            text = ollama.generate('hi', system='be kind')
        path, payload = srv.requests[0]
        self.assertEqual(path, '/api/generate')
        self.assertEqual((payload['model'], payload['stream'], payload['prompt'], payload['system']),
                         ('m1', False, 'hi', 'be kind'))
        self.assertEqual(text, 'Hello 4.00')

    def test_server_down_is_a_safe_error(self):
        with override_settings(AI_OLLAMA_URL='http://127.0.0.1:9', AI_OLLAMA_TIMEOUT=2):
            with self.assertRaises(ollama.OllamaUnavailable):
                ollama.generate('hi')

    def test_bad_answer_is_a_safe_error(self):
        with FakeOllamaServer(status=200, answer='   ') as srv, override_settings(AI_OLLAMA_URL=srv.url):
            with self.assertRaises(ollama.OllamaUnavailable):
                ollama.generate('hi')

    def test_switched_off_never_calls_the_network(self):
        with override_settings(AI_OLLAMA_ENABLED=False):
            with mock.patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
                with self.assertRaises(ollama.OllamaUnavailable):
                    ollama.generate('hi')

    def test_made_up_numbers_are_rejected(self):
        facts = "Overall score: 4.00 out of 5. Responses: 12."
        self.assertTrue(ollama.numbers_are_supported("You scored 4.00 from 12 students, out of 5.", facts))
        self.assertFalse(ollama.numbers_are_supported("You scored 4.87.", facts))
        self.assertFalse(ollama.numbers_are_supported("Based on 40 students.", facts))

    def test_write_text_rejects_unsupported_numbers(self):
        with mock.patch.object(ollama, 'generate', return_value="Your score is 4.99 now."):
            with self.assertRaises(ollama.OllamaUnavailable):
                ollama.write_text("Overall score: 4.00 out of 5.")


class SuggestionPipelineTests(TestCase):
    def setUp(self):
        self.teacher = make_user('t@test.com', 'instructor')
        sentiment._cache.clear()

    def prepare(self):
        build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        rate(make_period(5), self.teacher, [4, 4, 4, 5],
             ["Secret opinion one about the class", "Another private remark", "Great class sir", "Thank you"])

    def run_all(self, **kw):
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            return pipeline.run_for_instructor(self.teacher, **kw)

    def test_rules_are_saved_for_all_five_categories(self):
        self.prepare()
        self.run_all()
        rules = Suggestion.objects.filter(instructor=self.teacher, source='rule')
        self.assertEqual(rules.count(), 5)
        self.assertEqual(rules.first().period, make_period.__globals__['EvaluationPeriod'].objects.get(term='midterm', semester='2nd'))
        self.assertFalse(Suggestion.objects.filter(source='ollama').exists())     # Ollama not asked

    def test_running_again_does_not_duplicate(self):
        self.prepare()
        self.run_all()
        self.run_all(force=True)
        self.assertEqual(Suggestion.objects.filter(source='rule').count(), 5)

    def test_no_history_means_no_suggestions_and_no_crash(self):
        report = self.run_all()
        self.assertEqual(report['suggestions'], 'no_data')
        self.assertEqual(Suggestion.objects.count(), 0)

    def test_ollama_text_is_saved_and_labelled_as_ai(self):
        self.prepare()
        with mock.patch.object(ollama, 'generate', fake_generate):
            self.run_all(use_llm=True)
        written = Suggestion.objects.filter(source='ollama')
        self.assertEqual(written.filter(category='overall').count(), 1)
        self.assertEqual(written.exclude(category='overall').count(), 5)
        self.assertTrue(all(w.llm_model for w in written))
        self.assertEqual(Suggestion.objects.filter(source='rule').count(), 5)      # rules are kept separate

    def test_ollama_is_asked_only_once_for_the_same_facts(self):
        self.prepare()
        fake = mock.Mock(side_effect=fake_generate)
        with mock.patch.object(ollama, 'generate', fake):
            self.run_all(use_llm=True)
            first_calls = fake.call_count
            self.run_all(use_llm=True)
        self.assertEqual(fake.call_count, first_calls)

    def test_ollama_down_falls_back_to_rules_only(self):
        self.prepare()
        with mock.patch.object(ollama, 'generate', side_effect=ollama.OllamaUnavailable('down')):
            report = self.run_all(use_llm=True)
        self.assertEqual(report['suggestions'], 'rules_only')
        self.assertEqual(Suggestion.objects.filter(source='rule').count(), 5)
        self.assertFalse(Suggestion.objects.filter(source='ollama').exists())

    def test_ollama_with_invented_numbers_is_not_saved(self):
        self.prepare()
        bad = json.dumps({'overall': 'Great! Your score is 4.99 now, well done.',
                          'categories': {'punctuality': 'Your punctuality reached 4.99 which is great news.'}})
        with mock.patch.object(ollama, 'generate', return_value=bad):
            self.run_all(use_llm=True)
        self.assertFalse(Suggestion.objects.filter(source='ollama').exists())

    def test_recommendation_prompt_has_only_numbers_and_topics_never_comments(self):
        self.prepare()
        prompts = []

        def spy(prompt, system=None, as_json=False, max_tokens=300):
            if system == ollama.RECOMMEND_SYSTEM:
                prompts.append(prompt)
            return fake_generate(prompt, system, as_json, max_tokens)
        with mock.patch.object(ollama, 'generate', side_effect=spy):
            self.run_all(use_llm=True)
        self.assertTrue(prompts)
        text = ' '.join(prompts)
        for secret in ("Secret opinion", "private remark", "Great class sir", "Secretname", "secret-school.com", '@'):
            self.assertNotIn(secret, text)

    def test_summary_prompt_has_cleaned_comments_but_no_emails_links_or_phones(self):
        build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        rate(make_period(5), self.teacher, [4, 4, 4, 5],
             ["Call me on 09171234567 about the class", "Mail jane@secret-school.com about grades",
              "See www.secret-school.com for notes", "Great class sir and kind"])
        prompts = []

        def spy(prompt, system=None, as_json=False, max_tokens=300):
            if system == ollama.SUMMARY_SYSTEM:
                prompts.append(prompt)
            return fake_generate(prompt, system, as_json, max_tokens)
        with mock.patch.object(ollama, 'generate', side_effect=spy):
            self.run_all(use_llm=True)
        text = ' '.join(prompts)
        self.assertIn('Great class sir', text)
        for secret in ('09171234567', 'jane@', 'secret-school'):
            self.assertNotIn(secret, text)


class TextQualityTests(SimpleTestCase):
    """The summary is never one sentence, and the suggestions do not all read the same."""
    SUBS = [{'label': 'Class starts or ends late', 'tone': 'problem', 'mentions': 2, 'key': 'late'},
            {'label': 'Friendly manner in class', 'tone': 'praise', 'mentions': 2, 'key': 'friendly'}]

    def test_rule_summary_has_at_least_four_sentences_and_real_numbers(self):
        from ai_insights.services import pipeline
        for seed in ('1-1', '2-5', '9-3'):
            text = pipeline.rule_summary_text(4, {'positive': 2, 'neutral': 0, 'negative': 2}, [], self.SUBS, seed=seed)
            self.assertGreaterEqual(text.count('. ') + 1, 4)
            self.assertIn('2 were positive', text)
            self.assertIn('class starts or ends late (2)', text)

    def test_an_even_split_is_called_mixed_not_critical(self):
        from ai_insights.services import pipeline
        text = pipeline.rule_summary_text(4, {'positive': 2, 'neutral': 0, 'negative': 2}, [], self.SUBS, seed='x')
        self.assertNotIn('critical', text)
        self.assertNotIn('problems they would like you to fix', text)

    def test_suggestion_wording_changes_between_instructors_and_categories(self):
        from ai_insights.services import suggestions
        cats = {k: 4.0 for k in ('teaching_effectiveness', 'communication_skills', 'classroom_management',
                                 'punctuality', 'student_engagement')}
        latest = {'categories': cats, 'responses': 5, 'label': 'Midterm'}
        texts = set()
        for seed in ('1-5', '2-5', '3-5', '4-5'):
            for item in suggestions.build_rule_suggestions(latest, None, None, [], [], seed=seed):
                texts.add(item['text'])
        self.assertGreater(len(texts), 8)          # 20 suggestions, many different sentences