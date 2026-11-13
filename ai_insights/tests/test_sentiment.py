import sys
import types
from unittest import mock

from django.test import SimpleTestCase, TestCase

from ai_insights.models import CommentSentiment, FeedbackSummary
from ai_insights.services import pipeline, sentiment, themes

from .factories import make_period, make_user, rate

GOOD = ["Great teacher, explains the lesson clearly", "Thank you sir, very good lessons"]
BAD = ["Always late to class", "He is late again and the class is boring"]


def fake_classifier(counter=None):
    """A stand-in for the Hugging Face model: same output shape, simple word rules."""
    def run(texts):
        if counter is not None:
            counter.append(len(texts))
        out = []
        for t in texts:
            low = t.lower()
            if any(w in low for w in ('great', 'good', 'thank')):
                out.append([{'label': 'positive', 'score': 0.93}, {'label': 'neutral', 'score': 0.05}])
            elif any(w in low for w in ('late', 'boring', 'rude')):
                out.append([{'label': 'negative', 'score': 0.88}, {'label': 'neutral', 'score': 0.09}])
            else:
                out.append([{'label': 'neutral', 'score': 0.7}, {'label': 'positive', 'score': 0.2}])
        return out
    return run


class CleanCommentTests(SimpleTestCase):
    def test_junk_is_rejected(self):
        for bad in (None, 5, '', '   ', '!!!', '12345', 'ok', 'aaaaaaaa', 'hhhhhhhh', '\x00\x01'):
            self.assertIsNone(sentiment.clean_comment(bad), repr(bad))

    def test_good_text_is_cleaned_not_changed_in_meaning(self):
        self.assertEqual(sentiment.clean_comment("  Very   good\n class \t"), "Very good class")
        self.assertEqual(sentiment.clean_comment("Magaling magturo si sir"), "Magaling magturo si sir")

    def test_very_long_text_is_cut(self):
        self.assertLessEqual(len(sentiment.clean_comment("good " * 1000)), sentiment.MAX_CHARS)


class ModelLoadingTests(SimpleTestCase):
    """The real loader code, using a fake `transformers` package."""

    def setUp(self):
        sentiment._cache.clear()
        self.addCleanup(sentiment._cache.clear)

    def fake_transformers(self, labels=None, boom=None):
        module = types.ModuleType('transformers')

        def pipeline_fn(task, **kwargs):
            if boom:
                raise boom
            clf = mock.MagicMock()
            clf.model.config.id2label = dict(enumerate(labels))
            clf.side_effect = lambda texts: [[{'label': labels[0], 'score': 0.9}] for _ in texts]
            module.kwargs = kwargs
            return clf
        module.pipeline = pipeline_fn
        return module

    def test_three_class_model_is_accepted_and_loaded_once(self):
        module = self.fake_transformers(['Positive', 'Neutral', 'Negative'])
        with mock.patch.dict(sys.modules, {'transformers': module}):
            first = sentiment.get_classifier('m')
            second = sentiment.get_classifier('m')
        self.assertIs(first, second)                      # cached: not loaded again
        self.assertTrue(module.kwargs['truncation'])

    def test_model_without_the_three_classes_is_refused(self):
        module = self.fake_transformers(['1 star', '2 stars', '3 stars', '4 stars', '5 stars'])
        with mock.patch.dict(sys.modules, {'transformers': module}):
            with self.assertRaises(sentiment.SentimentUnavailable) as ctx:
                sentiment.get_classifier('stars-model')
        self.assertIn('does not give positive / neutral / negative', str(ctx.exception))

    def test_label_0_1_2_models_are_refused_not_guessed(self):
        module = self.fake_transformers(['LABEL_0', 'LABEL_1', 'LABEL_2'])
        with mock.patch.dict(sys.modules, {'transformers': module}):
            with self.assertRaises(sentiment.SentimentUnavailable):
                sentiment.get_classifier('unknown-labels')

    def test_loading_failure_becomes_a_safe_message(self):
        module = self.fake_transformers([], boom=OSError('no internet'))
        with mock.patch.dict(sys.modules, {'transformers': module}):
            with self.assertRaises(sentiment.SentimentUnavailable) as ctx:
                sentiment.get_classifier('m')
        self.assertNotIn('no internet', str(ctx.exception))         # no internal detail leaks

    def test_missing_package_becomes_a_safe_message(self):
        with mock.patch.dict(sys.modules, {'transformers': None}):
            with self.assertRaises(sentiment.SentimentUnavailable):
                sentiment.get_classifier('m')

    def test_classify_reads_the_best_label_and_checks_it(self):
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            result = sentiment.classify(["Great class", "so boring", "okay lang"])
        self.assertEqual([r[0] for r in result], ['positive', 'negative', 'neutral'])
        self.assertTrue(all(0 <= r[1] <= 1 for r in result))
        self.assertEqual(sentiment.classify([]), [])

    def test_running_error_becomes_unavailable(self):
        broken = mock.Mock(side_effect=RuntimeError('cuda out of memory'))
        with mock.patch.object(sentiment, 'get_classifier', return_value=broken):
            with self.assertRaises(sentiment.SentimentUnavailable):
                sentiment.classify(["Great class"])

    def test_unexpected_label_is_refused(self):
        weird = mock.Mock(return_value=[[{'label': 'happy', 'score': 0.9}]])
        with mock.patch.object(sentiment, 'get_classifier', return_value=weird):
            with self.assertRaises(sentiment.SentimentUnavailable):
                sentiment.classify(["Great class"])


class ThemeTests(SimpleTestCase):
    def test_theme_needs_two_comments(self):
        found = themes.find_themes([("Always late", 'negative'), ("Great lesson", 'positive')])
        self.assertEqual(found, [])        # each topic has only one comment -> hidden (privacy)

    def test_counts_and_tone(self):
        found = themes.find_themes([("Always late", 'negative'), ("late again", 'negative'),
                                    ("sometimes late but fine", 'neutral')])
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]['key'], found[0]['mentions'], found[0]['negative']), ('punctuality', 3, 2))

    def test_words_inside_other_words_do_not_match(self):
        self.assertEqual(themes.find_themes([("translate this", 'neutral'), ("tranquilate", 'neutral')]), [])

    def test_filipino_words_work(self):
        found = themes.find_themes([("Laging huli si sir", 'negative'), ("nahuhuli palagi", 'negative')])
        self.assertEqual(found[0]['key'], 'punctuality')

    def test_no_text_leaves_the_function(self):
        found = themes.find_themes([("Always late secretname", 'negative'), ("late late", 'negative')])
        self.assertNotIn('secretname', str(found).lower())


class FeedbackPipelineTests(TestCase):
    def setUp(self):
        self.teacher = make_user('t@test.com', 'instructor')
        self.period = make_period(0)
        sentiment._cache.clear()

    def run_feedback(self, comments, scores=None, **kw):
        rate(self.period, self.teacher, scores or [4] * len(comments), comments)
        n = len(comments)
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier(kw.pop('counter', None))):
            return pipeline.run_feedback(self.teacher, self.period, n, **kw)

    def test_summary_counts_and_themes(self):
        self.assertEqual(self.run_feedback(GOOD + BAD), 'created')
        s = FeedbackSummary.objects.get(instructor=self.teacher, period=self.period)
        self.assertFalse(s.hidden)
        self.assertEqual((s.positive, s.neutral, s.negative), (2, 0, 2))
        self.assertEqual((s.n_comments, s.n_analyzed, s.n_skipped), (4, 4, 0))
        self.assertIn('punctuality', {t['key'] for t in s.themes})
        self.assertEqual(s.summary_source, 'rule')
        self.assertEqual(CommentSentiment.objects.count(), 4)

    def test_fewer_than_3_students_stays_hidden_and_nothing_is_analysed(self):
        with mock.patch.object(sentiment, 'get_classifier', side_effect=AssertionError('must not run')):
            rate(self.period, self.teacher, [5, 5], GOOD)
            self.assertEqual(pipeline.run_feedback(self.teacher, self.period, 2), 'too_few_students')
        s = FeedbackSummary.objects.get()
        self.assertTrue(s.hidden)
        self.assertEqual(CommentSentiment.objects.count(), 0)
        self.assertEqual((s.positive, s.negative, s.themes, s.summary_text), (0, 0, [], ''))

    def test_fewer_than_3_usable_comments_stays_hidden(self):
        self.assertEqual(self.run_feedback(["Great class", "ok", "", "!!!"]), 'too_few_comments')
        s = FeedbackSummary.objects.get()
        self.assertTrue(s.hidden)
        self.assertEqual(s.summary_text, '')

    def test_invalid_comments_are_skipped_and_counted(self):
        self.run_feedback(GOOD + BAD + ["", "aaaaaaaa", "???"])
        s = FeedbackSummary.objects.get()
        self.assertEqual((s.n_analyzed, s.n_skipped), (4, 2))       # empty comments are not "written"

    def test_comment_text_and_identity_are_never_stored(self):
        self.run_feedback(GOOD + BAD)
        dumped = ' '.join(str(v) for row in FeedbackSummary.objects.values() for v in row.values())
        dumped += ' '.join(str(v) for row in CommentSentiment.objects.values() for v in row.values())
        for secret in GOOD + BAD + ['Secretname', 'secret-school.com']:
            self.assertNotIn(secret, dumped)

    def test_model_is_not_run_again_for_unchanged_data(self):
        calls = []
        self.run_feedback(GOOD + BAD, counter=calls)
        self.assertEqual(calls, [4])
        with mock.patch.object(sentiment, 'get_classifier', side_effect=AssertionError('must not run')):
            self.assertEqual(pipeline.run_feedback(self.teacher, self.period, 4), 'skipped')

    def test_force_reuses_saved_sentiment_without_running_the_model_again(self):
        self.run_feedback(GOOD + BAD)
        with mock.patch.object(sentiment, 'get_classifier', side_effect=AssertionError('must not run')):
            self.assertEqual(pipeline.run_feedback(self.teacher, self.period, 4, force=True), 'created')

    def test_model_failure_hides_the_summary_and_does_not_crash(self):
        rate(self.period, self.teacher, [4] * 4, GOOD + BAD)
        with mock.patch.object(sentiment, 'get_classifier', side_effect=sentiment.SentimentUnavailable('not installed')):
            self.assertEqual(pipeline.run_feedback(self.teacher, self.period, 4), 'model_unavailable')
        s = FeedbackSummary.objects.get()
        self.assertTrue(s.hidden)
        self.assertIn('not installed', s.hidden_reason)
        self.assertEqual(s.input_hash, '')                  # so the next run tries again
        self.assertEqual(CommentSentiment.objects.count(), 0)

    def test_one_summary_per_instructor_and_period(self):
        self.run_feedback(GOOD + BAD)
        with mock.patch.object(sentiment, 'get_classifier', return_value=fake_classifier()):
            pipeline.run_feedback(self.teacher, self.period, 4, force=True)
        self.assertEqual(FeedbackSummary.objects.count(), 1)
