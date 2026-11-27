# Tests for the numbers at the top of the Admin Dashboard (users/dashboard_data.py).

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Department, Section, Subject, TeachingAssignment
from ai_insights.services import pipeline
from ai_insights.tests.factories import build_history, make_user, rate, spread
from evaluation.models import Evaluation, EvaluationPeriod
from evaluation.submission import create_evaluation
from users import dashboard_data

TODAY = timezone.now().date()
YEAR = '2026-2027'


def day(n):
    return TODAY + datetime.timedelta(days=n)


def make_period(term, semester='1st', start=-60, end=-50, active=False, year=YEAR):
    return EvaluationPeriod.objects.create(school_year=year, semester=semester, term=term,
                                           start_date=day(start), end_date=day(end), is_active=active)


def answer(student, teacher, period, score):
    """A real evaluation with its 15 answers (the period does not have to be open in tests)."""
    create_evaluation(student, teacher, period, scores=score, check_period_open=False)


class DashboardBase(TestCase):
    def setUp(self):
        # the migrations already make a few departments (for example Information Technology)
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.bus = Department.objects.create(code='BUS', name='Business')
        self.empty = Department.objects.create(code='HM', name='Hospitality')
        self.sec1 = Section.objects.create(name='S1', program='it', department=self.it)
        self.sec2 = Section.objects.create(name='S2', program='it', department=self.it)
        self.subjects = {}

        self.admin = make_user('admin@test.com', 'admin')
        self.a1 = make_user('a1@test.com', 'instructor', department=self.it)
        self.a2 = make_user('a2@test.com', 'instructor', department=self.it)
        self.b1 = make_user('b1@test.com', 'instructor', department=self.bus)
        self.s1 = [make_user(f's1{i}@test.com', 'student', section=self.sec1) for i in range(4)]
        self.s2 = [make_user(f's2{i}@test.com', 'student', section=self.sec2) for i in range(2)]

        for teacher in (self.a1, self.a2):                      # both teach section 1
            self.assign(teacher, self.sec1)
        self.assign(self.b1, self.sec2)                         # section 2 has one instructor

    def assign(self, teacher, section, semester='1st'):
        subject = self.subjects.get(teacher.pk) or Subject.objects.create(
            code=f'X{teacher.pk}', name='Subject', department=self.it)
        self.subjects[teacher.pk] = subject
        TeachingAssignment.objects.create(teacher=teacher, subject=subject, section=section,
                                          school_year=YEAR, semester=semester)


class CountsTests(DashboardBase):
    def test_totals_and_per_department_counts(self):
        d = dashboard_data.build()
        self.assertEqual(d['stats'], {'students': 6, 'instructors': 3, 'departments': Department.objects.count()})
        rows = {r['name']: r['instructors'] for r in d['departments']}
        self.assertEqual(len(rows), Department.objects.count())
        self.assertEqual((rows['Business'], rows['Hospitality'], rows[self.it.name]), (1, 0, 2))

    def test_inactive_accounts_are_not_counted(self):
        self.s1[0].is_active = False
        self.s1[0].save()
        self.assertEqual(dashboard_data.build()['stats']['students'], 5)

    def test_instructor_without_department_gets_its_own_row(self):
        make_user('nodept@test.com', 'instructor')
        names = [r['name'] for r in dashboard_data.build()['departments']]
        self.assertIn('No department', names)


class PeriodChoiceTests(DashboardBase):
    def test_current_period_is_used_when_one_is_open(self):
        make_period('prelims', start=-60, end=-50)
        current = make_period('midterm', start=-3, end=10, active=True)
        d = dashboard_data.build()
        self.assertTrue(d['is_current'])
        self.assertEqual(d['period']['term'], 'Midterm')
        self.assertEqual(d['period']['status'], 'ongoing')
        self.assertEqual(current.school_year, d['period']['school_year'])

    def test_no_open_period_shows_the_latest_one_with_results(self):
        old = make_period('prelims')
        newer = make_period('midterm', start=-40, end=-30)
        make_period('finals', start=-20, end=-10)                 # newest, but nobody answered
        answer(self.s1[0], self.a1, old, 4)
        answer(self.s1[0], self.a2, newer, 4)
        d = dashboard_data.build()
        self.assertFalse(d['is_current'])
        self.assertEqual(d['period']['term'], 'Midterm')
        self.assertEqual(d['period']['status'], 'closed')

    def test_expired_but_still_switched_on_is_not_current(self):
        make_period('prelims', start=-30, end=-5, active=True)    # the admin forgot to close it
        d = dashboard_data.build()
        self.assertFalse(d['is_current'])
        self.assertEqual(d['period']['status'], 'closed')

    def test_not_started_status_and_upcoming_note(self):
        old = make_period('prelims')
        answer(self.s1[0], self.a1, old, 4)
        make_period('midterm', start=5, end=15)                   # not switched on, begins later
        d = dashboard_data.build()
        self.assertFalse(d['is_current'])
        self.assertEqual(d['upcoming']['words'].split(' · ')[0], 'Midterm')
        self.assertEqual(dashboard_data.period_status(EvaluationPeriod.objects.get(term='midterm'), TODAY), 'not_started')
        started_early = make_period('finals', start=7, end=20, active=True)
        d = dashboard_data.build()
        self.assertTrue(d['is_current'])
        self.assertEqual(d['period']['status'], 'not_started')
        self.assertEqual(started_early.term, 'finals')

    def test_nothing_at_all(self):
        d = dashboard_data.build()
        self.assertIsNone(d['period'])
        self.assertIsNone(d['progress'])
        self.assertEqual(d['school']['average'], None)


class ProgressTests(DashboardBase):
    def test_required_completed_remaining_and_percent(self):
        p = make_period('prelims', start=-3, end=10, active=True)
        # section 1: 4 students x 2 instructors = 8 ; section 2: 2 students x 1 instructor = 2
        answer(self.s1[0], self.a1, p, 4)
        answer(self.s1[0], self.a2, p, 4)
        answer(self.s1[1], self.a1, p, 4)
        answer(self.s2[0], self.b1, p, 5)
        progress = dashboard_data.build()['progress']
        self.assertEqual((progress['required'], progress['completed'], progress['remaining']), (10, 4, 6))
        self.assertEqual(progress['percent'], 40.0)
        self.assertEqual(dashboard_data.build()['period']['completion'], '40')

    def test_other_semester_assignments_are_not_required(self):
        self.assign(self.a1, self.sec2, semester='2nd')            # teaches section 2 only next semester
        p = make_period('prelims', start=-3, end=10, active=True)
        self.assertEqual(dashboard_data.build()['progress']['required'], 10)
        self.assertEqual(p.semester, '1st')

    def test_evaluation_for_someone_who_does_not_teach_the_student_is_not_counted(self):
        p = make_period('prelims', start=-3, end=10, active=True)
        answer(self.s2[0], self.a1, p, 4)                          # a1 does not teach section 2
        self.assertEqual(dashboard_data.build()['progress']['completed'], 0)

    def test_nothing_required_gives_no_percent(self):
        TeachingAssignment.objects.all().delete()
        make_period('prelims', start=-3, end=10, active=True)
        progress = dashboard_data.build()['progress']
        self.assertEqual(progress['required'], 0)
        self.assertIsNone(progress['percent'])
        self.assertIsNone(dashboard_data.build()['period']['completion'])


class BestAndPoorTests(DashboardBase):
    def fill(self, period, teacher, score, n):
        pool = [make_user(f'x{teacher.pk}{period.pk}{i}@test.com', 'student', section=self.sec1) for i in range(n)]
        for student in pool:
            answer(student, teacher, period, score)

    def test_best_and_poor_per_department_with_scores_and_counts(self):
        p = make_period('prelims', start=-3, end=10, active=True)
        self.fill(p, self.a1, 5, 4)
        self.fill(p, self.a2, 3, 3)
        it = {r['name']: r for r in dashboard_data.build()['departments']}[self.it.name]
        self.assertEqual((it['best']['name'], it['best']['overall'], it['best']['count']), ('A1 Test', 5.0, 4))
        self.assertEqual((it['poor']['name'], it['poor']['overall'], it['poor']['count']), ('A2 Test', 3.0, 3))

    def test_instructor_with_too_few_evaluations_is_left_out(self):
        p = make_period('prelims', start=-3, end=10, active=True)
        self.fill(p, self.a1, 4, 3)
        self.fill(p, self.a2, 1, 2)                                 # lowest score, but only 2 answers
        it = {r['name']: r for r in dashboard_data.build()['departments']}[self.it.name]
        self.assertEqual(it['best']['name'], 'A1 Test')
        self.assertIsNone(it['poor'])                               # nobody else qualifies
        self.assertTrue(it['only_one'])

    def test_department_without_enough_data_has_neither(self):
        p = make_period('prelims', start=-3, end=10, active=True)
        self.fill(p, self.a1, 4, 3)
        rows = {r['name']: r for r in dashboard_data.build()['departments']}
        self.assertIsNone(rows['Business']['best'])
        self.assertIsNone(rows['Hospitality']['best'])

    def test_only_the_shown_period_is_used(self):
        old = make_period('prelims', start=-60, end=-50)
        current = make_period('midterm', start=-3, end=10, active=True)
        self.fill(old, self.a2, 5, 3)                               # a2 was great last time
        self.fill(current, self.a1, 4, 3)
        self.fill(current, self.a2, 2, 3)
        it = {r['name']: r for r in dashboard_data.build()['departments']}[self.it.name]
        self.assertEqual(it['best']['name'], 'A1 Test')
        self.assertEqual(it['poor']['name'], 'A2 Test')
        self.assertEqual(it['poor']['overall'], 2.0)


class SchoolAverageTests(DashboardBase):
    def fill(self, period, score, n=3):
        pool = [make_user(f'y{period.pk}{score}{i}@test.com', 'student', section=self.sec1) for i in range(n)]
        for s in pool:
            answer(s, self.a1, period, score)

    def test_average_and_improving(self):
        old = make_period('prelims', start=-60, end=-50)
        cur = make_period('midterm', start=-3, end=10, active=True)
        self.fill(old, 3)
        self.fill(cur, 4)
        school = dashboard_data.build()['school']
        self.assertEqual((school['average'], school['trend'], school['change']), (4.0, 'improving', 1.0))
        self.assertTrue(school['partial'])

    def test_declining_and_stable(self):
        old = make_period('prelims', start=-60, end=-50)
        cur = make_period('midterm', start=-40, end=-30)
        self.fill(old, 4)
        self.fill(cur, 3)
        self.assertEqual(dashboard_data.build()['school']['trend'], 'declining')
        answer(self.s2[0], self.a1, cur, 4)                         # 3,3,3,4 -> 3.25 : still lower
        self.assertEqual(dashboard_data.build()['school']['trend'], 'declining')

    def test_tiny_change_is_stable(self):
        old = make_period('prelims', start=-60, end=-50)
        cur = make_period('midterm', start=-40, end=-30)
        for i, score in enumerate([4] * 40):
            answer(make_user(f'z{i}@test.com', 'student', section=self.sec1), self.a1, old, score)
        for i, score in enumerate([4] * 39 + [5]):                  # 4.025 vs 4.00 : smaller than 0.05
            answer(make_user(f'w{i}@test.com', 'student', section=self.sec1), self.a1, cur, score)
        self.assertEqual(dashboard_data.build()['school']['trend'], 'stable')

    def test_first_period_has_nothing_to_compare(self):
        cur = make_period('prelims', start=-40, end=-30)
        self.fill(cur, 4)
        self.assertEqual(dashboard_data.build()['school']['trend'], 'none')

    def test_comparison_skips_periods_without_results(self):
        old = make_period('prelims', start=-80, end=-70)
        make_period('midterm', start=-60, end=-50)                  # nobody answered
        cur = make_period('prefinals', start=-40, end=-30)
        self.fill(old, 3)
        self.fill(cur, 4)
        school = dashboard_data.build()['school']
        self.assertEqual(school['previous']['average'], 3.0)


class PageTests(DashboardBase):
    def test_admin_page_with_current_period(self):
        make_period('midterm', start=-3, end=10, active=True)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for text in ('Total students', 'Total departments', 'Evaluation progress', 'School overall average',
                     'Current evaluation period', 'Ongoing', 'Poor performing'):
            self.assertIn(text, html)
        self.assertNotIn('No evaluation period is open right now', html)

    def test_fallback_page_names_year_semester_and_period(self):
        old = make_period('prefinals', semester='2nd', year='2025-2026')
        answer(self.s1[0], self.a1, old, 4)
        self.client.force_login(self.admin)
        html = self.client.get(reverse('admin_dashboard')).content.decode()
        self.assertIn('Latest available evaluation period', html)
        self.assertIn('No evaluation period is open right now', html)
        self.assertIn('2025-2026', html)
        self.assertIn('Second Semester', html)
        self.assertIn('Pre-Finals', html)
        self.assertIn('Closed', html)

    def test_empty_system_page_still_opens(self):
        EvaluationPeriod.objects.all().delete()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('No evaluation period yet', r.content.decode())

    def test_other_roles_cannot_open_it(self):
        for user in (self.a1, self.s1[0]):
            self.client.force_login(user)
            r = self.client.get(reverse('admin_dashboard'))
            self.assertEqual(r.status_code, 302)

    def test_page_shows_no_student_names_or_emails(self):
        p = make_period('midterm', start=-3, end=10, active=True)
        answer(self.s1[0], self.a1, p, 4)
        self.client.force_login(self.admin)
        html = self.client.get(reverse('admin_dashboard')).content.decode()
        self.assertNotIn('s10@test.com', html)


class ForecastOutlookTests(TestCase):
    def setUp(self):
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.admin = make_user('admin@test.com', 'admin')
        self.rising = make_user('rise@test.com', 'instructor', department=self.it)
        self.falling = make_user('fall@test.com', 'instructor', department=self.it)
        self.periods = build_history(self.rising, [3.0, 3.25, 3.5, 3.75], responses=4)
        for period, score in zip(self.periods, [4.5, 4.0, 3.5, 3.0]):          # same 4 periods, 2nd instructor
            rate(period, self.falling, spread(score, 4))

    def test_without_saved_forecasts_the_real_trend_shows_and_nothing_is_guessed(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_dashboard'))
        outlook = r.context['outlook']
        self.assertIsNotNone(outlook['line_chart'])
        self.assertFalse(outlook['has_forecast'])
        self.assertEqual(outlook['bars'], [])
        self.assertIn('No forecast is saved', r.content.decode())

    def test_saved_forecast_gives_bars_and_a_dashed_line(self):
        pipeline.run_for_all(use_sentiment=False)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_dashboard'))
        outlook = r.context['outlook']
        self.assertTrue(outlook['has_forecast'])
        bar = outlook['bars'][0]
        self.assertEqual(bar['forecasts'], 2)
        self.assertEqual(bar['avg_last'], 3.38)                      # mean of 3.75 and 3.0
        self.assertIsNotNone(bar['avg_pred'])
        chart = outlook['line_chart']
        dashed = [line for line in chart['lines'] if line['dashed']]
        self.assertEqual(len(dashed), 1)
        self.assertEqual(chart['labels'][-1], 'Prelims · 2nd Sem 24-25 (forecast)')
        html = r.content.decode()
        self.assertIn('stroke-dasharray', html)
        self.assertIn('Forecast for the next period', html)

    def test_forecast_made_from_an_older_period_is_not_shown_as_new(self):
        pipeline.run_for_all(use_sentiment=False)
        from ai_insights.tests.factories import make_period as make_old
        newer = make_old(4)
        rate(newer, self.rising, [4, 4, 4, 4])
        rate(newer, self.falling, [3, 3, 3, 3])
        outlook = __import__('ai_insights.selectors', fromlist=['x']).department_outlook()
        self.assertFalse(outlook['has_forecast'])                    # the saved forecasts are one period behind
