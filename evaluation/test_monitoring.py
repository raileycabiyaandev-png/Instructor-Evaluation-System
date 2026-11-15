# evaluation/test_monitoring.py
# Tests for the Admin "Evaluation Monitoring" page (evaluation/monitoring.py + the two views).
# They make REAL rows in the temporary test database, so the numbers are really counted.

import datetime
import io

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from academics.models import Department, Section, Subject, TeachingAssignment
from evaluation import monitoring
from evaluation.models import Evaluation, EvaluationPeriod
from users.models import User

TODAY = timezone.now().date()
HASH = make_password('Pass-12345!')
SECRET_COMMENT = 'PRIVATE-COMMENT-DO-NOT-SHOW'


def user(email, role, **extra):
    u = User(email=email, username=email, role=role, first_name=email.split('@')[0].title(),
             last_name='Test', must_change_password=False, **extra)
    u.password = HASH
    u.save()
    return u


def period(year='2026-2027', sem='1st', term='prelims', active=True, start=-2, end=10):
    return EvaluationPeriod.objects.create(
        school_year=year, semester=sem, term=term, is_active=active,
        start_date=TODAY + datetime.timedelta(days=start), end_date=TODAY + datetime.timedelta(days=end))


def evaluate(student, teacher, p, comment=''):
    return Evaluation.objects.create(
        student=student, teacher=teacher, period=p, comment=comment,
        teaching_effectiveness=4, communication_skills=4, classroom_management=4, punctuality=4, student_engagement=4)


def assign(teacher, subject, section, year='2026-2027', sem='1st'):
    return TeachingAssignment.objects.create(teacher=teacher, subject=subject, section=section,
                                             school_year=year, semester=sem)


class World(TestCase):
    """
    Dept A: instructor T1 (teaches 2 subjects to S1).
    Dept B: instructor T2 (teaches S1 and S2).
    S1 has students s1, s2, s4.   S2 has student s3.
    Required: S1 = T1 + T2 for 3 students = 6, S2 = T2 for 1 student = 1  ->  7.
    Done: s1->T1, s1->T2, s2->T1, s3->T2 = 4.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = user('admin@x.com', 'admin')
        cls.dept_a = Department.objects.create(code='MA', name='Monitoring Dept A')
        cls.dept_b = Department.objects.create(code='MB', name='Monitoring Dept B')
        cls.s1 = Section.objects.create(name='MON 1A', program='it', department=cls.dept_a)
        cls.s2 = Section.objects.create(name='MON 1B', program='it', department=cls.dept_a)
        cls.sub1 = Subject.objects.create(code='IT101', name='Programming', department=cls.dept_a)
        cls.sub2 = Subject.objects.create(code='IT102', name='Databases', department=cls.dept_a)
        cls.sub3 = Subject.objects.create(code='GE101', name='English', department=cls.dept_b)
        cls.t1 = user('t1@x.com', 'instructor', department=cls.dept_a)
        cls.t2 = user('t2@x.com', 'instructor', department=cls.dept_b)
        cls.p = period()

        assign(cls.t1, cls.sub1, cls.s1)
        assign(cls.t1, cls.sub2, cls.s1)          # same instructor, 2nd subject, same section: must count ONCE
        assign(cls.t2, cls.sub3, cls.s1)
        assign(cls.t2, cls.sub3, cls.s2)

        cls.st1 = user('st1@x.com', 'student', section=cls.s1)
        cls.st2 = user('st2@x.com', 'student', section=cls.s1)
        cls.st3 = user('st3@x.com', 'student', section=cls.s2)
        cls.st4 = user('st4@x.com', 'student', section=cls.s1)
        evaluate(cls.st1, cls.t1, cls.p, SECRET_COMMENT)
        evaluate(cls.st1, cls.t2, cls.p)
        evaluate(cls.st2, cls.t1, cls.p)
        evaluate(cls.st3, cls.t2, cls.p)

    def get_data(self, **params):
        return monitoring.build(monitoring.parse_filters(params))


class CountingTests(World):
    def test_overall_numbers_come_from_real_records(self):
        o = self.get_data()['stats']['overall']
        self.assertEqual((o['required'], o['completed'], o['remaining']), (7, 4, 3))
        self.assertEqual(o['percent'], 57.1)

    def test_two_subjects_with_same_instructor_in_one_section_count_once(self):
        rows = {i['name']: i for i in self.get_data()['stats']['instructors']}
        t1 = rows['Test, T1']
        self.assertEqual(t1['required'], 3)            # 3 students of S1, NOT 6
        self.assertEqual(t1['completed'], 2)
        self.assertEqual(t1['sections'], 1)

    def test_two_instructors_in_one_section_are_two_evaluations(self):
        rows = {i['name']: i for i in self.get_data()['stats']['instructors']}
        self.assertEqual(rows['Test, T2']['required'], 4)   # s1, s2, s4 (S1) + s3 (S2)
        self.assertEqual(rows['Test, T2']['completed'], 2)

    def test_student_statuses(self):
        stats = self.get_data()['stats']
        by_name = {s['name']: s['status'] for s in stats['students']}
        self.assertEqual(by_name['Test, St1'], 'completed')
        self.assertEqual(by_name['Test, St2'], 'partial')
        self.assertEqual(by_name['Test, St3'], 'completed')
        self.assertEqual(by_name['Test, St4'], 'not_started')
        self.assertEqual(stats['student_counts'], {'completed': 2, 'partial': 1, 'not_started': 1})

    def test_departments_and_comparison(self):
        depts = {d['name']: d for d in self.get_data()['stats']['departments']}
        self.assertEqual(depts['Monitoring Dept A']['required'], 3)
        self.assertEqual(depts['Monitoring Dept A']['completed'], 2)
        self.assertEqual(depts['Monitoring Dept B']['required'], 4)
        self.assertEqual(depts['Monitoring Dept B']['completed'], 2)
        # IT 66.7% is above the school 57.1%, GE 50% is below
        self.assertEqual(depts['Monitoring Dept A']['compare'], 'above')
        self.assertEqual(depts['Monitoring Dept B']['compare'], 'below')

    def test_other_periods_are_not_counted(self):
        other = period(term='midterm', active=False, start=-60, end=-50)
        evaluate_inactive = Evaluation(student=self.st4, teacher=self.t1, period=other,
                                       teaching_effectiveness=5, communication_skills=5, classroom_management=5,
                                       punctuality=5, student_engagement=5)
        super(Evaluation, evaluate_inactive).save()          # skip the "period must be open" check on purpose
        self.assertEqual(self.get_data()['stats']['overall']['completed'], 4)

    def test_other_semester_assignments_are_not_required(self):
        assign(self.t1, self.sub1, self.s2, sem='2nd')
        self.assertEqual(self.get_data()['stats']['overall']['required'], 7)

    def test_inactive_students_and_instructors_are_left_out(self):
        User.objects.filter(pk=self.st4.pk).update(is_active=False)
        self.assertEqual(self.get_data()['stats']['overall']['required'], 5)
        User.objects.filter(pk=self.t1.pk).update(is_active=False)
        self.assertEqual(self.get_data()['stats']['overall']['required'], 3)      # only T2 is left: s1, s2, s3

    def test_evaluation_for_an_instructor_the_student_no_longer_has_is_not_counted(self):
        extra = user('t3@x.com', 'instructor', department=self.dept_a)
        evaluate(self.st4, extra, self.p)                    # t3 teaches nobody in S1
        self.assertEqual(self.get_data()['stats']['overall']['completed'], 4)

    def test_nothing_required_gives_none_not_a_fake_percent(self):
        TeachingAssignment.objects.all().delete()
        o = self.get_data()['stats']['overall']
        self.assertEqual((o['required'], o['completed'], o['percent']), (0, 0, None))
        self.assertEqual(o['percent_text'], '—')

    def test_counts_are_not_hardcoded(self):
        evaluate(self.st4, self.t1, self.p)
        self.assertEqual(self.get_data()['stats']['overall']['completed'], 5)


class FilterTests(World):
    def test_department_filter_uses_the_instructors_department(self):
        o = self.get_data(department=self.dept_a.pk)['stats']['overall']
        self.assertEqual((o['required'], o['completed']), (3, 2))

    def test_section_filter(self):
        o = self.get_data(section=self.s2.pk)['stats']['overall']
        self.assertEqual((o['required'], o['completed']), (1, 1))

    def test_instructor_filter(self):
        data = self.get_data(instructor=self.t2.pk)['stats']
        self.assertEqual((data['overall']['required'], data['overall']['completed']), (4, 2))
        self.assertEqual(data['instructor_count'], 1)

    def test_filters_work_together(self):
        o = self.get_data(department=self.dept_b.pk, section=self.s1.pk)['stats']['overall']
        self.assertEqual((o['required'], o['completed']), (3, 1))

    def test_bad_filter_values_are_ignored(self):
        o = self.get_data(department='abc', section='-4', instructor='99999', semester='zzz')['stats']['overall']
        self.assertEqual(o['required'], 7)

    def test_an_admin_id_is_not_accepted_as_an_instructor_filter(self):
        f = monitoring.parse_filters({'instructor': self.admin.pk})
        self.assertIsNone(f.instructor)

    def test_period_filters(self):
        later = period(year='2026-2027', sem='2nd', term='midterm', active=False, start=40, end=60)
        data = self.get_data(school_year='2026-2027', semester='2nd', term='midterm')
        self.assertEqual(data['period']['pk'], later.pk)
        self.assertEqual(data['stats']['overall']['required'], 0)

    def test_missing_combination_gives_no_period(self):
        data = self.get_data(school_year='1999-2000')
        self.assertIsNone(data['period'])
        self.assertTrue(data['has_periods'])

    def test_no_period_at_all(self):
        EvaluationPeriod.objects.all().delete()
        data = self.get_data()
        self.assertIsNone(data['period'])
        self.assertFalse(data['has_periods'])

    def test_student_status_filter(self):
        rows = self.get_data()['stats']['students']
        self.assertEqual([r['name'] for r in monitoring.apply_student_filter(rows, 'partial')], ['Test, St2'])
        self.assertEqual(len(monitoring.apply_student_filter(rows, '')), 4)


class PeriodTests(TestCase):
    def test_remaining_days_and_status(self):
        today = TODAY
        ongoing = monitoring.period_block(period(start=-2, end=10), today)
        self.assertEqual((ongoing['status'], ongoing['timing']), ('ongoing', '10 days remaining'))
        self.assertEqual(ongoing['days_left'], 10)

        last = monitoring.period_block(period(term='midterm', start=-5, end=0), today)
        self.assertEqual(last['timing'], 'Ends today')

        one = monitoring.period_block(period(term='prefinals', start=-5, end=1), today)
        self.assertEqual(one['timing'], '1 day remaining')

        future = monitoring.period_block(period(term='finals', active=False, start=3, end=9), today)
        self.assertEqual((future['status'], future['timing']), ('not_started', 'Starts in 3 days'))

        closed = monitoring.period_block(period(sem='2nd', active=False, start=-30, end=-4), today)
        self.assertEqual((closed['status'], closed['timing']), ('closed', 'Closed 4 days ago'))

    def test_switched_off_early_is_not_called_remaining(self):
        off = monitoring.period_block(period(active=False, start=-2, end=10), TODAY)
        self.assertEqual(off['status'], 'closed')
        self.assertEqual(off['timing'], 'Switched off by the administrator')
        self.assertIsNone(off['days_left'])

    def test_default_is_the_open_period(self):
        old = period(year='2025-2026', active=False, start=-300, end=-290)
        current = period(year='2026-2027', start=-1, end=5)
        data = monitoring.build(monitoring.parse_filters({}))
        self.assertEqual(data['period']['pk'], current.pk)
        self.assertTrue(data['is_default'])
        self.assertNotEqual(data['period']['pk'], old.pk)


class QueryCountTests(World):
    """The number of database queries must not grow with the number of students (no N+1)."""

    def count(self):
        with CaptureQueriesContext(connection) as ctx:
            monitoring.calculate(self.p, monitoring.Filters())
        return len(ctx)

    def test_queries_do_not_grow_with_students(self):
        small = self.count()
        for i in range(40):
            user(f'bulk{i}@x.com', 'student', section=self.s1 if i % 2 else self.s2)
        self.assertEqual(self.count(), small)
        self.assertLessEqual(small, 5)


class PageTests(World):
    url = None

    def setUp(self):
        self.url = reverse('evaluation_monitoring')

    def login(self, who):
        self.client.force_login(who)

    def test_admin_can_open(self):
        self.login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Evaluation monitoring')
        self.assertContains(response, '57.1%')
        self.assertContains(response, '10 days remaining')

    def test_only_admin_is_allowed(self):
        for url in (self.url, reverse('evaluation_monitoring_export') + '?fmt=pdf'):
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)                      # not logged in
            for who in (self.st1, self.t1):
                self.login(who)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302, f"{who.role} must not open {url}")
                self.assertEqual(response['Location'], reverse('login'))

    def test_page_never_shows_comments_or_ratings(self):
        self.login(self.admin)
        html = self.client.get(self.url).content.decode()
        self.assertNotIn(SECRET_COMMENT, html)
        self.assertNotIn('Teaching Effectiveness', html)

    def test_page_with_all_filters_and_bad_values_does_not_crash(self):
        self.login(self.admin)
        for query in ('?department=%s&section=%s&instructor=%s' % (self.dept_a.pk, self.s1.pk, self.t1.pk),
                      '?school_year=1999-2000', '?ipage=999&spage=abc', '?status=hacker', '?department=<script>',
                      '?semester=2nd&term=finals'):
            response = self.client.get(self.url + query)
            self.assertEqual(response.status_code, 200, query)

    def test_status_tab_and_pagination(self):
        for i in range(30):
            user(f'late{i}@x.com', 'student', section=self.s2)
        self.login(self.admin)
        response = self.client.get(self.url + '?status=not_started&spage=2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['students_page'].number, 2)
        self.assertContains(response, 'Showing 16')

    def test_empty_states(self):
        self.login(self.admin)
        TeachingAssignment.objects.all().delete()
        self.assertContains(self.client.get(self.url), 'No evaluations are required')
        self.assertContains(self.client.get(self.url + '?school_year=1999-2000'), 'No evaluation period matches')
        EvaluationPeriod.objects.all().delete()
        self.assertContains(self.client.get(self.url), 'No evaluation period yet')

    def test_sidebar_has_the_link(self):
        self.login(self.admin)
        self.assertContains(self.client.get(reverse('manage_evaluations')), reverse('evaluation_monitoring'))


class ExportTests(World):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('evaluation_monitoring_export')

    def test_excel_has_the_real_numbers_and_no_student_names(self):
        from openpyxl import load_workbook
        response = self.client.get(self.url + '?fmt=xlsx')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        book = load_workbook(io.BytesIO(response.content))
        text = ' '.join(str(c.value) for sheet in book for row in sheet.iter_rows() for c in row if c.value is not None)
        self.assertIn('57.1%', text)
        self.assertIn('Test, T1', text)
        self.assertNotIn('St4', text)                          # a student name
        self.assertNotIn(SECRET_COMMENT, text)

    def test_pdf(self):
        response = self.client.get(self.url + '?fmt=pdf&department=%s' % self.dept_a.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertIn('attachment', response['Content-Disposition'])

    def test_bad_format_goes_back_with_a_message(self):
        response = self.client.get(self.url + '?fmt=exe')
        self.assertRedirects(response, reverse('evaluation_monitoring'), fetch_redirect_response=False)

    def test_export_without_any_period_still_works(self):
        EvaluationPeriod.objects.all().delete()
        self.assertEqual(self.client.get(self.url + '?fmt=pdf').status_code, 200)


class SectionTests(World):
    """Progress by section + the organised student list."""

    def sections(self, **params):
        return {x['name']: x for x in self.get_data(**params)['stats']['sections']}

    def test_section_numbers(self):
        rows = self.sections()
        a, b = rows['MON 1A'], rows['MON 1B']
        # MON 1A: 3 students x 2 instructors (T1 counted once even with 2 subjects) = 6, done = s1:2 + s2:1
        self.assertEqual((a['students'], a['required'], a['completed'], a['remaining']), (3, 6, 3, 3))
        self.assertEqual((a['completed_students'], a['partial_students'], a['not_started_students']), (1, 1, 1))
        self.assertEqual(a['percent'], 50.0)
        self.assertEqual((b['students'], b['required'], b['completed']), (1, 1, 1))
        self.assertEqual(b['percent'], 100.0)
        self.assertEqual(b['department'], 'Monitoring Dept A')

    def test_section_totals_match_the_overall_numbers(self):
        data = self.get_data()['stats']
        self.assertEqual(sum(x['required'] for x in data['sections']), data['overall']['required'])
        self.assertEqual(sum(x['completed'] for x in data['sections']), data['overall']['completed'])
        self.assertEqual(sum(x['students'] for x in data['sections']), data['students_total'])

    def test_filters_narrow_the_sections(self):
        self.assertEqual(list(self.sections(section=self.s2.pk)), ['MON 1B'])
        self.assertEqual(set(self.sections(instructor=self.t1.pk)), {'MON 1A'})       # T1 teaches only MON 1A
        self.assertEqual(set(self.sections(department=self.dept_b.pk)), {'MON 1A', 'MON 1B'})

    def test_sorting(self):
        rows = self.get_data()['stats']['sections']
        names = lambda how: [x['name'] for x in monitoring.sort_sections(rows, how)]
        self.assertEqual(names('low'), ['MON 1A', 'MON 1B'])        # 50% then 100%
        self.assertEqual(names('high'), ['MON 1B', 'MON 1A'])
        self.assertEqual(names('name'), ['MON 1A', 'MON 1B'])
        self.assertEqual(names('nonsense'), ['MON 1A', 'MON 1B'])   # unknown = lowest first

    def test_student_search_and_section_scope(self):
        rows = self.get_data()['stats']['students']
        scope = monitoring.students_in_scope
        self.assertEqual({r['name'] for r in scope(rows, self.s2.pk)}, {'Test, St3'})
        self.assertEqual({r['name'] for r in scope(rows, None, 'st2')}, {'Test, St2'})
        self.assertEqual({r['name'] for r in scope(rows, None, 'TEST st4')}, {'Test, St4'})   # any case, many words
        self.assertEqual(scope(rows, self.s1.pk, 'st3'), [])                                  # St3 is not in MON 1A
        self.assertEqual(len(scope(rows)), 4)
        counts = monitoring.count_statuses(scope(rows, self.s1.pk))
        self.assertEqual(counts, {'completed': 1, 'partial': 1, 'not_started': 1})

    def test_new_filters_are_checked(self):
        f = monitoring.parse_filters({'ssection': 'abc', 'q': '  <b>x</b>   y ' + 'z' * 100, 'ssort': 'hack'})
        self.assertIsNone(f.student_section)
        self.assertEqual(f.section_sort, '')
        self.assertLessEqual(len(f.q), 50)
        self.assertTrue(f.q.startswith('<b>x</b> y'))             # kept as plain text; the template escapes it

    def test_queries_still_do_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                monitoring.calculate(self.p, monitoring.Filters())
            return len(ctx)
        small = count()
        for i in range(12):
            sec = Section.objects.create(name=f'BULK {i}', program='it', department=self.dept_a)
            assign(self.t1, self.sub1, sec)
            user(f'bk{i}@x.com', 'student', section=sec)
        self.assertEqual(count(), small)


class SectionPageTests(World):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('evaluation_monitoring')

    def test_section_table_is_on_the_page(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Progress by section')
        self.assertContains(response, 'MON 1A')
        self.assertContains(response, 'View students')
        self.assertContains(response, 'ssection=%s' % self.s1.pk)

    def test_view_students_shows_only_that_section(self):
        response = self.client.get(self.url + '?ssection=%s' % self.s2.pk)
        names = [r['name'] for r in response.context['students_page']]
        self.assertEqual(names, ['Test, St3'])
        # the school numbers do NOT change when you only look at one section's students
        self.assertContains(response, '57.1%')

    def test_name_search_and_status_together(self):
        response = self.client.get(self.url + '?q=st&status=partial&ssection=%s' % self.s1.pk)
        self.assertEqual([r['name'] for r in response.context['students_page']], ['Test, St2'])
        self.assertEqual(response.context['student_counts'], {'completed': 1, 'partial': 1, 'not_started': 1})

    def test_search_text_is_escaped(self):
        html = self.client.get(self.url + '?q=%3Cscript%3Ealert(1)%3C/script%3E').content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('No students found', html)

    def test_section_pagination_and_sorting(self):
        for i in range(14):
            sec = Section.objects.create(name=f'PAGE {i:02d}', program='it', department=self.dept_a)
            assign(self.t1, self.sub1, sec)
            user(f'pg{i}@x.com', 'student', section=sec)
        response = self.client.get(self.url + '?secpage=2&ssort=name')
        page = response.context['sections_page']
        self.assertEqual(page.number, 2)
        self.assertEqual(page.paginator.count, 16)
        self.assertEqual(len(page.object_list), 6)
        self.assertContains(response, 'sections')
        self.assertEqual(self.client.get(self.url + '?secpage=999&ssort=zzz').status_code, 200)

    def test_export_has_a_sections_sheet_without_student_names(self):
        from openpyxl import load_workbook
        response = self.client.get(reverse('evaluation_monitoring_export') + '?fmt=xlsx')
        book = load_workbook(io.BytesIO(response.content))
        self.assertTrue(any('Sections' in name for name in book.sheetnames))
        sheet = next(book[n] for n in book.sheetnames if 'Sections' in n)
        text = ' '.join(str(c.value) for row in sheet.iter_rows() for c in row if c.value is not None)
        self.assertIn('MON 1A', text)
        self.assertIn('50%', text)
        self.assertNotIn('St2', text)