from django.test import SimpleTestCase, TestCase

from ai_insights.models import ForecastResult
from ai_insights.services import forecasting, pipeline
from ai_insights.services.data import instructor_series

from .factories import build_history, make_period, make_user, rate, spread


class FakePeriod:
    def __init__(self, term, i):
        self.term, self.pk, self.school_year, self.semester = term, i, '2024-2025', '1st'

    def get_term_display(self):
        return self.term

    def get_semester_display(self):
        return 'First Semester'


def fake_series(values):
    terms = ['prelims', 'midterm', 'prefinals', 'finals']
    out = []
    for i, v in enumerate(values):
        cats = {f: v for f, _ in forecasting.CATEGORIES}
        out.append({'period': FakePeriod(terms[i % 4], i), 'label': f'P{i}', 'responses': 5,
                    'overall': v, 'categories': cats})
    return out


class MethodTests(SimpleTestCase):
    """The maths, without a database."""

    def test_insufficient_history_gives_no_number(self):
        for n in (0, 1, 2, 3):
            result = forecasting.make_forecast(fake_series([4.0] * n))
            self.assertNotEqual(result['status'], 'ok')
            self.assertNotIn('predicted_overall', result)
            self.assertNotIn('predicted_categories', result)

    def test_zero_periods_is_not_enough_data(self):
        result = forecasting.make_forecast(fake_series([]))
        self.assertEqual(result['status'], 'insufficient')
        self.assertIn('nothing to learn from', result['message'])

    def test_one_period_is_only_a_baseline(self):
        result = forecasting.make_forecast(fake_series([4.2]))
        self.assertEqual(result['status'], 'baseline')
        self.assertIn('Baseline only', result['message'])
        self.assertIn('4.20', result['message'])               # the one real score is shown
        self.assertNotIn('predicted_overall', result)

    def test_two_or_three_periods_are_not_enough(self):
        for n in (2, 3):
            result = forecasting.make_forecast(fake_series([4.0] * n))
            self.assertEqual(result['status'], 'insufficient')
            self.assertIn('Not enough history', result['message'])

    def test_a_model_with_too_big_test_error_is_not_shown(self):
        wild = fake_series([1.0, 5.0, 1.0, 5.0, 1.0, 5.0])      # jumps around: nobody can predict this
        result = forecasting.make_forecast(wild)
        self.assertEqual(result['status'], 'unreliable')
        self.assertNotIn('predicted_overall', result)
        self.assertIn('allowed limit', result['message'])
        self.assertGreater(result['validation_mae'], 0.6)

    def test_the_error_limit_can_be_changed_in_settings(self):
        from django.test import override_settings
        wild = fake_series([1.0, 5.0, 1.0, 5.0, 1.0, 5.0])
        with override_settings(AI_MAX_VALIDATION_MAE=4.0):
            self.assertEqual(forecasting.make_forecast(wild)['status'], 'ok')

    def test_next_period_follows_prelims_midterm_prefinals_finals_then_next_semester(self):
        from types import SimpleNamespace as P
        cases = [
            (P(term='prelims', semester='1st', school_year='2025-2026'), ('midterm', '1st', '2025-2026')),
            (P(term='midterm', semester='1st', school_year='2025-2026'), ('prefinals', '1st', '2025-2026')),
            (P(term='prefinals', semester='1st', school_year='2025-2026'), ('finals', '1st', '2025-2026')),
            (P(term='finals', semester='1st', school_year='2025-2026'), ('prelims', '2nd', '2025-2026')),
            (P(term='finals', semester='2nd', school_year='2025-2026'), ('prelims', '1st', '2026-2027')),
        ]
        for last, expected in cases:
            info = forecasting.next_period_info(last)
            self.assertEqual((info['term'], info['semester'], info['school_year']), expected)

    def test_straight_line_is_continued(self):
        result = forecasting.make_forecast(fake_series([3.0, 3.2, 3.4, 3.6, 3.8]))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['model_name'], 'trend')
        self.assertAlmostEqual(result['predicted_overall'], 4.0, delta=0.15)
        self.assertEqual(result['direction'], 'up')

    def test_flat_history_predicts_flat(self):
        result = forecasting.make_forecast(fake_series([4.0, 4.0, 4.0, 4.0]))
        self.assertAlmostEqual(result['predicted_overall'], 4.0, places=2)
        self.assertEqual(result['direction'], 'steady')

    def test_prediction_stays_between_1_and_5(self):
        up = forecasting.make_forecast(fake_series([3.6, 4.0, 4.4, 4.8, 5.0]))
        down = forecasting.make_forecast(fake_series([2.4, 2.0, 1.6, 1.2, 1.0]))
        self.assertLessEqual(up['predicted_overall'], 5.0)
        self.assertGreaterEqual(down['predicted_overall'], 1.0)
        for r in (up, down):
            self.assertTrue(1.0 <= r['range_low'] <= r['range_high'] <= 5.0)

    def test_chosen_model_is_never_worse_than_the_simple_guess(self):
        for values in ([3.0, 4.5, 3.2, 4.4, 3.1, 4.6], [4.1, 4.0, 4.2, 3.9, 4.0], [2.0, 2.5, 3.0, 3.6, 4.1, 4.5]):
            r = forecasting.make_forecast(fake_series(values))
            self.assertLessEqual(r['validation_mae'], r['baseline_mae'] + 1e-9)

    def test_no_data_leakage_later_values_never_change_earlier_test_errors(self):
        """
        Add (or change) values at the END of the history. The test errors of the
        EARLIER steps must stay exactly the same. If they moved, the model would be
        peeking at the future.
        """
        short = [3.0, 3.4, 3.1, 3.8, 3.5]
        terms = [1, 2, 3, 4, 1, 2, 3]
        for tail in ([4.9, 1.1], [3.0, 3.0]):
            long = short + tail
            for model in forecasting.MODEL_ORDER:
                early = forecasting.walk_forward_errors(model, short, terms)
                later = forecasting.walk_forward_errors(model, long, terms)
                self.assertEqual(early, later[:len(early)], msg=model)

    def test_prediction_for_k_ignores_the_value_at_k(self):
        a = [3.0, 3.4, 3.1, 3.8, 3.5]
        b = [3.0, 3.4, 3.1, 1.0, 3.5]                 # only the value being predicted differs
        terms = [1, 2, 3, 4, 1]
        for model in forecasting.MODEL_ORDER:
            self.assertAlmostEqual(forecasting._predict_one(model, a, terms, 3),
                                   forecasting._predict_one(model, b, terms, 3), places=9, msg=model)

    def test_walk_forward_step_count(self):
        self.assertEqual(len(forecasting.walk_forward_errors('last', [4.0] * 4, [1, 2, 3, 4])), 1)
        self.assertEqual(len(forecasting.walk_forward_errors('last', [4.0] * 8, [1, 2, 3, 4] * 2)), 5)

    def test_small_history_is_marked_low_reliability(self):
        self.assertEqual(forecasting.make_forecast(fake_series([3.0, 3.2, 3.4, 3.6]))['reliability'], 'low')
        self.assertEqual(forecasting.make_forecast(fake_series([3.0, 3.1, 3.2, 3.3, 3.4, 3.5]))['reliability'], 'fair')

    def test_next_period_label(self):
        class P:
            def __init__(self, term, sem, year):
                self.term, self.semester, self.school_year = term, sem, year
        self.assertIn('Midterm', forecasting.next_period_label(P('prelims', '1st', '2024-2025')))
        self.assertIn('Second Semester', forecasting.next_period_label(P('finals', '1st', '2024-2025')))
        self.assertIn('2025-2026', forecasting.next_period_label(P('finals', '2nd', '2024-2025')))


class RealDataTests(TestCase):
    """The same, but reading REAL rows from the database."""

    def setUp(self):
        self.teacher = make_user('t@test.com', 'instructor')
        self.other = make_user('o@test.com', 'instructor')

    def test_only_finished_periods_with_enough_responses_are_used(self):
        build_history(self.teacher, [3.0, 3.5])
        thin = make_period(2)
        rate(thin, self.teacher, [5, 5])                 # only 2 responses -> too noisy
        live = make_period(3, active=True)
        rate(live, self.teacher, [5, 5, 5])              # still open -> not final
        series = instructor_series(self.teacher)
        self.assertEqual([round(s['overall'], 2) for s in series], [3.0, 3.5])

    def test_not_enough_periods_is_saved_as_a_message_not_a_number(self):
        build_history(self.teacher, [3.0, 3.5, 4.0])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        row = ForecastResult.objects.get(instructor=self.teacher)
        self.assertEqual(row.status, 'insufficient')
        self.assertIsNone(row.predicted_overall)
        self.assertIn('3 finished period', row.message)

    def test_forecast_is_saved_with_model_info_and_real_history(self):
        periods = build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        row = ForecastResult.objects.get(instructor=self.teacher, status='ok')
        self.assertEqual(row.based_on_period, periods[-1])
        self.assertEqual(row.periods_used, 5)
        self.assertEqual([h['overall'] for h in row.history], [3.0, 3.25, 3.5, 3.75, 4.0])
        self.assertAlmostEqual(row.predicted_overall, 4.25, delta=0.2)
        self.assertTrue(row.model_name and row.library_version.startswith('scikit-learn'))
        self.assertEqual(len(row.predicted_categories), 5)
        self.assertIsNotNone(row.generated_at)

    def test_prediction_is_not_hardcoded_it_follows_the_data(self):
        build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        for i, v in enumerate([4.5, 4.0, 3.5, 3.0, 2.5]):
            rate(make_period(i), self.other, spread(v, 4))
        pipeline.run_for_all(use_sentiment=False)
        up = ForecastResult.objects.get(instructor=self.teacher).predicted_overall
        down = ForecastResult.objects.get(instructor=self.other).predicted_overall
        self.assertGreater(up, 4.0)
        self.assertLess(down, 3.0)

    def test_same_data_is_not_recomputed(self):
        build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        first = pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        again = pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        self.assertEqual(first['forecast'], 'created')
        self.assertEqual(again['forecast'], 'skipped')
        self.assertEqual(ForecastResult.objects.filter(instructor=self.teacher).count(), 1)
        forced = pipeline.run_for_instructor(self.teacher, use_sentiment=False, force=True)
        self.assertEqual(forced['forecast'], 'created')
        self.assertEqual(ForecastResult.objects.filter(instructor=self.teacher).count(), 1)

    def test_new_finished_period_makes_a_new_forecast_and_keeps_the_old_one(self):
        build_history(self.teacher, [3.0, 3.25, 3.5, 3.75])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        rate(make_period(4), self.teacher, [4, 4, 4, 4])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        self.assertEqual(ForecastResult.objects.filter(instructor=self.teacher, status='ok').count(), 2)

    def test_insufficient_row_is_replaced_when_data_grows(self):
        build_history(self.teacher, [3.0, 3.25, 3.5])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        self.assertEqual(ForecastResult.objects.get(instructor=self.teacher).status, 'insufficient')
        rate(make_period(3), self.teacher, [4, 4, 4, 4])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        rows = ForecastResult.objects.filter(instructor=self.teacher)
        self.assertEqual(rows.count(), 2)                      # one row per period: the old note + the new forecast
        from ai_insights import selectors
        self.assertEqual(selectors._forecast_block(self.teacher)['state'], 'ok')   # pages show the newest period

    def test_a_crash_inside_the_forecast_is_saved_as_error_and_does_not_stop_the_run(self):
        from unittest import mock
        build_history(self.teacher, [3.0, 3.25, 3.5, 3.75, 4.0])
        with mock.patch.object(forecasting, 'make_forecast', side_effect=RuntimeError('boom')):
            report = pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        self.assertEqual(report['forecast'], 'error')
        row = ForecastResult.objects.get(instructor=self.teacher)
        self.assertEqual(row.status, 'error')
        self.assertNotIn('boom', row.message)            # no internal detail is shown

    def test_one_forecast_per_instructor_and_period_is_enforced(self):
        from django.db import IntegrityError, transaction
        periods = build_history(self.teacher, [3.0, 3.25, 3.5, 3.75])
        pipeline.run_for_instructor(self.teacher, use_sentiment=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ForecastResult.objects.create(instructor=self.teacher, based_on_period=periods[-1], status='ok')