# evaluation/test_student_flow.py
#
# Run with:   python manage.py test evaluation.test_student_flow
# (uses a temporary database - your real data is not touched)

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Department, Section, Subject, TeachingAssignment
from evaluation.models import Evaluation, EvaluationPeriod
from users.models import User

PASSWORD = 'Student-pass-123'
RATINGS = {
    'teaching_effectiveness': 5, 'communication_skills': 4, 'classroom_management': 3,
    'punctuality': 5, 'student_engagement': 4, 'comment': 'Great class',
}


def make_user(email, role, **extra):
    extra.setdefault('must_change_password', False)
    user = User(email=email, username=email, role=role,
                first_name=email.split('@')[0].title(), last_name='Test', **extra)
    user.set_password(PASSWORD)
    user.save()
    return user


class StudentBase(TestCase):
    def setUp(self):
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.ge, _ = Department.objects.get_or_create(code='GENED', defaults={'name': 'General Education'})

        self.sec_a = Section.objects.create(name='BSIT 2A', program='it', department=self.it)
        self.sec_b = Section.objects.create(name='BSIT 2B', program='it', department=self.it)

        self.web = Subject.objects.create(code='IT101', name='Web Dev', department=self.it)
        self.db = Subject.objects.create(code='IT102', name='Databases', department=self.it)
        self.math = Subject.objects.create(code='GE101', name='Math', department=self.ge)
        self.eng = Subject.objects.create(code='GE102', name='English', department=self.ge)

        self.prof_it = make_user('itprof@test.com', 'instructor', department=self.it)
        self.prof_ge = make_user('geprof@test.com', 'instructor', department=self.ge)
        self.prof_b = make_user('bprof@test.com', 'instructor', department=self.it)       # teaches only 2B
        self.prof_old = make_user('oldprof@test.com', 'instructor', department=self.it)   # only last semester
        self.prof_off = make_user('offprof@test.com', 'instructor', department=self.it, is_active=False)

        today = timezone.now().date()
        self.period = EvaluationPeriod.objects.create(
            school_year='2026-2027', semester='1st', term='prelims',
            start_date=today - datetime.timedelta(days=5), end_date=today + datetime.timedelta(days=5),
            is_active=True,
        )

        def assign(teacher, subject, section, semester='1st'):
            TeachingAssignment.objects.create(teacher=teacher, subject=subject, section=section,
                                              school_year='2026-2027', semester=semester)

        assign(self.prof_it, self.web, self.sec_a)
        assign(self.prof_it, self.db, self.sec_a)        # same teacher, 2nd subject
        assign(self.prof_ge, self.math, self.sec_a)      # GenEd teacher teaching an IT section
        assign(self.prof_b, self.web, self.sec_b)
        assign(self.prof_old, self.web, self.sec_a, semester='2nd')
        assign(self.prof_off, self.eng, self.sec_a)

        self.student = make_user('stud@test.com', 'student', program='it', section=self.sec_a)
        self.classmate = make_user('mate@test.com', 'student', program='it', section=self.sec_a)
        self.client.login(username='stud@test.com', password=PASSWORD)

    def evaluate_url(self, teacher):
        return reverse('evaluate_teacher_page', args=[teacher.pk, self.period.pk])

    def submit_url(self, teacher):
        return reverse('submit_evaluation', args=[teacher.pk, self.period.pk])


class DashboardShowsOnlyMyInstructors(StudentBase):

    def test_only_instructors_of_my_section_and_semester(self):
        context = self.client.get(reverse('student_dashboard')).context
        teachers = {c['teacher'] for c in context['pending_cards'] + context['evaluated_cards']}
        self.assertEqual(teachers, {self.prof_it, self.prof_ge})           # not 2B, not old semester, not inactive
        self.assertEqual(context['total_instructors'], 2)
        self.assertEqual(context['state'], 'ready')

    def test_teacher_with_two_subjects_appears_once(self):
        context = self.client.get(reverse('student_dashboard')).context
        card = next(c for c in context['pending_cards'] if c['teacher'] == self.prof_it)
        self.assertEqual({s.code for s in card['subjects']}, {'IT101', 'IT102'})
        self.assertEqual(len(context['pending_cards']), 2)

    def test_evaluated_and_not_evaluated_are_both_shown(self):
        Evaluation.objects.create(student=self.student, teacher=self.prof_ge, period=self.period,
                                  teaching_effectiveness=5, communication_skills=5, classroom_management=5,
                                  punctuality=5, student_engagement=5)
        context = self.client.get(reverse('student_dashboard')).context
        self.assertEqual([c['teacher'] for c in context['evaluated_cards']], [self.prof_ge])
        self.assertEqual([c['teacher'] for c in context['pending_cards']], [self.prof_it])
        self.assertEqual(context['progress_percent'], 50)

    def test_other_students_evaluations_do_not_count_for_me(self):
        Evaluation.objects.create(student=self.classmate, teacher=self.prof_ge, period=self.period,
                                  teaching_effectiveness=5, communication_skills=5, classroom_management=5,
                                  punctuality=5, student_engagement=5)
        context = self.client.get(reverse('student_dashboard')).context
        self.assertEqual(context['evaluated_count'], 0)

    def test_student_without_section(self):
        self.student.section = None
        self.student.save()
        context = self.client.get(reverse('student_dashboard')).context
        self.assertEqual(context['state'], 'no_section')
        self.assertEqual(context['total_instructors'], 0)

    def test_section_without_assignments(self):
        empty = Section.objects.create(name='BSIT 1A', program='it', department=self.it)
        self.student.section = empty
        self.student.save()
        self.assertEqual(self.client.get(reverse('student_dashboard')).context['state'], 'no_assignments')

    def test_no_open_period(self):
        EvaluationPeriod.objects.update(is_active=False)
        context = self.client.get(reverse('student_dashboard')).context
        self.assertEqual(context['state'], 'no_period')
        self.assertEqual(context['total_instructors'], 0)

    def test_expired_period_is_closed_automatically(self):
        EvaluationPeriod.objects.update(end_date=timezone.now().date() - datetime.timedelta(days=1))
        context = self.client.get(reverse('student_dashboard')).context
        self.assertEqual(context['state'], 'no_period')
        self.period.refresh_from_db()
        self.assertFalse(self.period.is_active)

    def test_moving_to_another_section_changes_the_list(self):
        self.student.section = self.sec_b
        self.student.save()
        context = self.client.get(reverse('student_dashboard')).context
        self.assertEqual([c['teacher'] for c in context['pending_cards']], [self.prof_b])

    def test_page_renders_with_instructor_names(self):
        response = self.client.get(reverse('student_dashboard'))
        self.assertContains(response, 'IT101')
        self.assertNotContains(response, 'Bprof')                          # instructor of another section


class QrAndEvaluateGuards(StudentBase):

    def qr(self, teacher):
        return self.client.get(reverse('qr_redirect_handler') + f'?teacher={teacher.pk}')

    def test_qr_of_my_instructor_opens_the_form(self):
        self.assertRedirects(self.qr(self.prof_it), self.evaluate_url(self.prof_it), fetch_redirect_response=False)

    def test_qr_of_instructor_from_another_section_is_refused(self):
        response = self.qr(self.prof_b)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Instructor Not In Your Section')

    def test_qr_of_last_semesters_or_inactive_instructor_is_refused(self):
        self.assertContains(self.qr(self.prof_old), 'Instructor Not In Your Section')
        self.assertContains(self.qr(self.prof_off), 'Instructor Not In Your Section')

    def test_bad_qr_values_do_not_crash(self):
        for value in ('', 'abc', '99999'):
            response = self.client.get(reverse('qr_redirect_handler') + f'?teacher={value}')
            self.assertIn(response.status_code, (302, 404))

    def test_typing_the_evaluate_address_for_a_foreign_instructor_is_refused(self):
        self.assertContains(self.client.get(self.evaluate_url(self.prof_b)), 'Instructor Not In Your Section')

    def test_submit_for_foreign_instructor_saves_nothing(self):
        response = self.client.post(self.submit_url(self.prof_b), RATINGS)
        self.assertContains(response, 'Instructor Not In Your Section')
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_student_without_section_is_refused_everywhere(self):
        self.student.section = None
        self.student.save()
        self.assertContains(self.qr(self.prof_it), 'No Section Assigned Yet')
        self.assertContains(self.client.get(self.evaluate_url(self.prof_it)), 'No Section Assigned Yet')
        self.client.post(self.submit_url(self.prof_it), RATINGS)
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_closed_period_blocks_form_and_submit(self):
        EvaluationPeriod.objects.update(is_active=False)
        self.assertContains(self.qr(self.prof_it), 'Evaluation Closed')
        self.assertContains(self.client.get(self.evaluate_url(self.prof_it)), 'Evaluation Closed')
        response = self.client.post(self.submit_url(self.prof_it), RATINGS)
        self.assertContains(response, 'Evaluation Closed')                 # a friendly page, not a crash
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_old_period_id_in_the_address_is_refused(self):
        old = EvaluationPeriod.objects.create(
            school_year='2025-2026', semester='1st', term='finals',
            start_date=datetime.date(2025, 10, 1), end_date=datetime.date(2025, 11, 1), is_active=False)
        response = self.client.post(reverse('submit_evaluation', args=[self.prof_it.pk, old.pk]), RATINGS)
        self.assertContains(response, 'Evaluation Closed')
        self.assertEqual(Evaluation.objects.count(), 0)


class SubmitEvaluation(StudentBase):

    def test_submit_saves_and_returns_to_dashboard(self):
        response = self.client.post(self.submit_url(self.prof_ge), RATINGS)
        self.assertRedirects(response, reverse('student_dashboard'), fetch_redirect_response=False)
        evaluation = Evaluation.objects.get()
        self.assertEqual((evaluation.student, evaluation.teacher, evaluation.period),
                         (self.student, self.prof_ge, self.period))

    def test_gened_teacher_of_it_section_can_be_evaluated_but_only_once(self):
        self.client.post(self.submit_url(self.prof_ge), RATINGS)
        response = self.client.post(self.submit_url(self.prof_ge), RATINGS)
        self.assertContains(response, 'Already Submitted')
        self.assertEqual(Evaluation.objects.count(), 1)

    def test_qr_for_already_evaluated_instructor_says_so(self):
        self.client.post(self.submit_url(self.prof_ge), RATINGS)
        response = self.client.get(reverse('qr_redirect_handler') + f'?teacher={self.prof_ge.pk}')
        self.assertContains(response, 'Already Submitted')

    def test_missing_or_invalid_ratings_are_refused(self):
        bad = dict(RATINGS, punctuality='9')
        response = self.client.post(self.submit_url(self.prof_ge), bad)
        self.assertRedirects(response, self.evaluate_url(self.prof_ge), fetch_redirect_response=False)
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_long_comment_is_trimmed(self):
        self.client.post(self.submit_url(self.prof_ge), dict(RATINGS, comment='x' * 5000))
        self.assertEqual(len(Evaluation.objects.get().comment), 1000)

    def test_get_request_is_refused(self):
        self.assertEqual(self.client.get(self.submit_url(self.prof_ge)).status_code, 405)

    def test_form_page_shows_period_and_subjects(self):
        response = self.client.get(self.evaluate_url(self.prof_it))
        self.assertContains(response, 'IT101')
        self.assertContains(response, '2026-2027')


class OnlyStudentsUseStudentPages(StudentBase):

    def test_instructor_and_admin_are_turned_away(self):
        make_user('admin@test.com', 'admin')
        for email in ('itprof@test.com', 'admin@test.com'):
            self.client.logout()
            self.client.login(username=email, password=PASSWORD)
            for url in (reverse('scan_qr_page'), self.evaluate_url(self.prof_it),
                        reverse('qr_redirect_handler') + f'?teacher={self.prof_it.pk}'):
                self.assertEqual(self.client.get(url).status_code, 302, (email, url))
            self.client.post(self.submit_url(self.prof_it), RATINGS)
        self.assertEqual(Evaluation.objects.count(), 0)


class ScanPage(StudentBase):

    def test_scan_page_names_the_chosen_instructor(self):
        response = self.client.get(reverse('scan_qr_page') + f'?teacher={self.prof_it.pk}')
        self.assertEqual(response.context['expected_teacher'], self.prof_it)
        self.assertContains(response, 'Itprof Test')

    def test_scan_page_ignores_an_instructor_of_another_section(self):
        response = self.client.get(reverse('scan_qr_page') + f'?teacher={self.prof_b.pk}')
        self.assertIsNone(response.context['expected_teacher'])

    def test_scan_page_blocked_when_closed_or_no_section(self):
        self.student.section = None
        self.student.save()
        self.assertContains(self.client.get(reverse('scan_qr_page')), 'No Section Assigned Yet')


class HistoryProfilePassword(StudentBase):

    def test_history_lists_only_my_evaluations(self):
        self.client.post(self.submit_url(self.prof_ge), RATINGS)
        Evaluation.objects.create(student=self.classmate, teacher=self.prof_it, period=self.period,
                                  teaching_effectiveness=1, communication_skills=1, classroom_management=1,
                                  punctuality=1, student_engagement=1)
        history = list(self.client.get(reverse('student_dashboard')).context['history'])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].teacher, self.prof_ge)

    def test_profile_update_changes_name_but_not_program_or_section(self):
        self.client.post(reverse('student_update_profile'), {
            'first_name': 'New', 'middle_name': 'M', 'last_name': 'Name', 'email': 'stud@test.com',
            'program': 'cs', 'section': self.sec_b.pk, 'role': 'admin',
        })
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'New')
        self.assertEqual(self.student.program, 'it')
        self.assertEqual(self.student.section, self.sec_a)
        self.assertEqual(self.student.role, 'student')

    def test_profile_update_rejects_email_used_by_someone_else(self):
        response = self.client.post(reverse('student_update_profile'), {
            'first_name': 'A', 'last_name': 'B', 'email': 'MATE@test.com'}, follow=True)
        self.assertEqual(response.status_code, 200)                        # no server error
        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'stud@test.com')
        self.assertContains(response, 'already used')

    def test_change_password(self):
        url = reverse('student_change_password')
        wrong = self.client.post(url, {'old_password': 'nope', 'new_password1': 'Brand-new-9xK!',
                                       'new_password2': 'Brand-new-9xK!'}, follow=True)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password(PASSWORD))

        weak = self.client.post(url, {'old_password': PASSWORD, 'new_password1': '12345',
                                      'new_password2': '12345'})
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password(PASSWORD))

        same = self.client.post(url, {'old_password': PASSWORD, 'new_password1': PASSWORD,
                                      'new_password2': PASSWORD}, follow=True)
        self.assertContains(same, 'must be different')

        good = self.client.post(url, {'old_password': PASSWORD, 'new_password1': 'Brand-new-9xK!',
                                      'new_password2': 'Brand-new-9xK!'}, follow=True)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password('Brand-new-9xK!'))
        self.assertEqual(self.client.get(reverse('student_dashboard')).status_code, 200)   # still logged in

    def test_other_roles_cannot_use_student_profile_endpoints(self):
        self.client.logout()
        self.client.login(username='itprof@test.com', password=PASSWORD)
        response = self.client.post(reverse('student_update_profile'), {
            'first_name': 'Hacked', 'last_name': 'X', 'email': 'itprof@test.com'})
        self.assertEqual(response.status_code, 302)
        self.prof_it.refresh_from_db()
        self.assertNotEqual(self.prof_it.first_name, 'Hacked')