# academics/tests.py
#
# Run with:   python manage.py test academics
#
# (These tests create their own temporary database, so your real data is not touched.)

import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from evaluation.models import EvaluationPeriod
from users.models import User

from .models import Department, Section, Subject, TeachingAssignment

PASSWORD = 'Temp-pass-123'


def make_user(email, role, **extra):
    user = User(email=email, username=email, role=role, first_name=email.split('@')[0].title(),
                last_name='Test', **extra)
    user.set_password(PASSWORD)
    user.save()
    return user


class AcademicBase(TestCase):
    def setUp(self):
        self.admin = make_user('admin@test.com', 'admin', must_change_password=False)
        # The migration already creates these two, so reuse them
        self.it, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        self.gened, _ = Department.objects.get_or_create(code='GENED', defaults={'name': 'General Education'})

        self.teacher_it = make_user('itprof@test.com', 'instructor', department=self.it,
                                    must_change_password=False)
        self.teacher_ge = make_user('geprof@test.com', 'instructor', department=self.gened,
                                    must_change_password=False)

        self.web = Subject.objects.create(code='IT101', name='Web Dev', department=self.it)
        self.math = Subject.objects.create(code='GE101', name='Math', department=self.gened)

        self.bsit2a = Section.objects.create(name='BSIT 2A', program='it', department=self.it)
        self.bsit2b = Section.objects.create(name='BSIT 2B', program='it', department=self.it)

        self.period = EvaluationPeriod.objects.create(
            school_year='2026-2027', semester='1st', term='prelims',
            start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2026, 9, 1),
            is_active=True,
        )

    def login_admin(self):
        self.client.login(username='admin@test.com', password=PASSWORD)


class ModelRelationshipTests(AcademicBase):

    def test_everything_is_connected(self):
        student = make_user('stud@test.com', 'student', program='it', section=self.bsit2a)
        TeachingAssignment.objects.create(
            teacher=self.teacher_ge, subject=self.math, section=self.bsit2a,
            school_year='2026-2027', semester='1st',
        )
        self.assertEqual(student.section.department, self.it)
        self.assertIn(student, self.bsit2a.students.all())
        self.assertIn(self.web, self.it.subjects.all())
        self.assertIn(self.teacher_it, self.it.users.all())
        self.assertEqual(self.teacher_ge.teaching_assignments.count(), 1)

    def test_gened_teacher_can_teach_it_students(self):
        assignment = TeachingAssignment(
            teacher=self.teacher_ge, subject=self.math, section=self.bsit2a,
            school_year='2026-2027', semester='1st',
        )
        assignment.full_clean()          # must NOT raise
        assignment.save()

    def test_teacher_can_have_many_sections(self):
        for section in (self.bsit2a, self.bsit2b):
            TeachingAssignment.objects.create(
                teacher=self.teacher_it, subject=self.web, section=section,
                school_year='2026-2027', semester='1st',
            )
        self.assertEqual(self.teacher_it.teaching_assignments.count(), 2)

    def test_same_subject_section_semester_has_one_teacher(self):
        TeachingAssignment.objects.create(
            teacher=self.teacher_it, subject=self.web, section=self.bsit2a,
            school_year='2026-2027', semester='1st',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeachingAssignment.objects.create(
                teacher=self.teacher_ge, subject=self.web, section=self.bsit2a,
                school_year='2026-2027', semester='1st',
            )

    def test_same_subject_section_in_another_semester_is_allowed(self):
        TeachingAssignment.objects.create(
            teacher=self.teacher_it, subject=self.web, section=self.bsit2a,
            school_year='2026-2027', semester='1st',
        )
        TeachingAssignment.objects.create(
            teacher=self.teacher_it, subject=self.web, section=self.bsit2a,
            school_year='2026-2027', semester='2nd',
        )

    def test_students_cannot_be_assigned_to_teach(self):
        student = make_user('stud@test.com', 'student')
        assignment = TeachingAssignment(
            teacher=student, subject=self.web, section=self.bsit2a,
            school_year='2026-2027', semester='1st',
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_department_with_subjects_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.it.delete()


class AccessControlTests(AcademicBase):

    def test_visitors_and_non_admins_cannot_open_academic_pages(self):
        student = make_user('stud@test.com', 'student', must_change_password=False)
        urls = [
            reverse('department_list'), reverse('subject_list'), reverse('section_list'),
            reverse('assignment_list'), reverse('section_students', args=[self.bsit2a.pk]),
            reverse('department_update', args=[self.it.pk]),
        ]

        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302, url)   # not logged in

        self.client.login(username='stud@test.com', password=PASSWORD)
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn('/', response['Location'])

        # a student can't POST a delete either
        self.client.post(reverse('department_delete', args=[self.gened.pk]))
        self.assertTrue(Department.objects.filter(pk=self.gened.pk).exists())

    def test_destructive_urls_need_post(self):
        self.login_admin()
        response = self.client.get(reverse('department_delete', args=[self.gened.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Department.objects.filter(pk=self.gened.pk).exists())


class AdminScreensTests(AcademicBase):

    def setUp(self):
        super().setUp()
        self.login_admin()

    def test_all_pages_open(self):
        for name, args in [
            ('department_list', []), ('subject_list', []), ('section_list', []),
            ('assignment_list', []), ('section_students', [self.bsit2a.pk]),
            ('department_update', [self.it.pk]), ('subject_update', [self.web.pk]),
            ('section_update', [self.bsit2a.pk]),
            ('create_student', []), ('create_instructor', []),
        ]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)

    def test_create_department_subject_section(self):
        self.client.post(reverse('department_list'), {'code': 'bus', 'name': 'Business', 'is_active': 'on'})
        business = Department.objects.get(code='BUS')                      # code is upper-cased

        self.client.post(reverse('subject_list'), {
            'code': 'bus101', 'name': 'Marketing', 'department': business.pk, 'is_active': 'on'})
        self.assertTrue(Subject.objects.filter(code='BUS101', department=business).exists())

        self.client.post(reverse('section_list'), {
            'name': 'bsit 3a', 'program': 'it', 'department': self.it.pk, 'is_active': 'on'})
        self.assertTrue(Section.objects.filter(name='BSIT 3A').exists())

    def test_duplicate_department_code_is_rejected(self):
        response = self.client.post(reverse('department_list'), {'code': 'it', 'name': 'Another', 'is_active': 'on'})
        self.assertEqual(response.status_code, 200)                         # form shown again with error
        self.assertEqual(Department.objects.filter(code='IT').count(), 1)

    def test_toggle_and_safe_delete(self):
        self.client.post(reverse('department_toggle', args=[self.it.pk]))
        self.it.refresh_from_db()
        self.assertFalse(self.it.is_active)

        # has subjects -> cannot delete, but nothing crashes
        response = self.client.post(reverse('department_delete', args=[self.it.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Department.objects.filter(pk=self.it.pk).exists())

        # empty department -> can delete
        empty = Department.objects.create(code='EMP', name='Empty')
        self.client.post(reverse('department_delete', args=[empty.pk]))
        self.assertFalse(Department.objects.filter(pk=empty.pk).exists())

    def test_inactive_department_not_offered_for_new_subject(self):
        self.gened.is_active = False
        self.gened.save()
        response = self.client.get(reverse('subject_list'))
        self.assertNotIn(self.gened, response.context['form'].fields['department'].queryset)


class SectionStudentsTests(AcademicBase):

    def setUp(self):
        super().setUp()
        self.login_admin()
        self.s1 = make_user('s1@test.com', 'student', program='it')
        self.s2 = make_user('s2@test.com', 'student', program='cs')       # different program

    def test_add_and_remove_students(self):
        url = reverse('section_students', args=[self.bsit2a.pk])
        self.client.post(url, {'action': 'add', 'students': [self.s1.pk, self.s2.pk]})
        self.s1.refresh_from_db(); self.s2.refresh_from_db()
        self.assertEqual(self.s1.section, self.bsit2a)
        self.assertIsNone(self.s2.section)                                 # BSCS student refused

        self.client.post(url, {'action': 'remove', 'student': self.s1.pk})
        self.s1.refresh_from_db()
        self.assertIsNone(self.s1.section)

    def test_student_already_in_another_section_is_not_stolen(self):
        self.s1.section = self.bsit2b
        self.s1.save()
        self.client.post(reverse('section_students', args=[self.bsit2a.pk]),
                         {'action': 'add', 'students': [self.s1.pk]})
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.section, self.bsit2b)

    def test_section_with_students_cannot_be_deleted(self):
        self.s1.section = self.bsit2a
        self.s1.save()
        self.client.post(reverse('section_delete', args=[self.bsit2a.pk]))
        self.assertTrue(Section.objects.filter(pk=self.bsit2a.pk).exists())


class TeachingAssignmentScreenTests(AcademicBase):

    def setUp(self):
        super().setUp()
        self.login_admin()

    def payload(self, **extra):
        data = {
            'teacher': self.teacher_ge.pk, 'subject': self.math.pk, 'section': self.bsit2a.pk,
            'semester_choice': '2026-2027|1st',
        }
        data.update(extra)
        return data

    def test_default_semester_comes_from_active_evaluation_period(self):
        response = self.client.get(reverse('assignment_list'))
        self.assertEqual(response.context['form'].fields['semester_choice'].initial, '2026-2027|1st')
        self.assertEqual(response.context['selected_semester'], '2026-2027|1st')

    def test_create_assignment_gened_teacher_for_it_section(self):
        response = self.client.post(reverse('assignment_list'), self.payload())
        self.assertEqual(response.status_code, 302)
        a = TeachingAssignment.objects.get()
        self.assertEqual((a.school_year, a.semester), ('2026-2027', '1st'))
        self.assertEqual(a.teacher.department, self.gened)
        self.assertEqual(a.section.department, self.it)

    def test_second_teacher_for_same_subject_section_semester_is_rejected_nicely(self):
        self.client.post(reverse('assignment_list'), self.payload())
        response = self.client.post(reverse('assignment_list'), self.payload(teacher=self.teacher_it.pk))
        self.assertEqual(response.status_code, 200)                        # error shown, no crash
        self.assertEqual(TeachingAssignment.objects.count(), 1)
        self.assertContains(response, 'already has a teacher')

    def test_semester_must_exist_in_evaluation_periods(self):
        response = self.client.post(reverse('assignment_list'), self.payload(semester_choice='1999-2000|1st'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TeachingAssignment.objects.count(), 0)

    def test_inactive_teacher_is_not_offered(self):
        self.teacher_it.is_active = False
        self.teacher_it.save()
        form = self.client.get(reverse('assignment_list')).context['form']
        self.assertNotIn(self.teacher_it, form.fields['teacher'].queryset)
        self.assertIn(self.teacher_ge, form.fields['teacher'].queryset)

    def test_student_accounts_are_never_offered_as_teachers(self):
        student = make_user('stud@test.com', 'student')
        form = self.client.get(reverse('assignment_list')).context['form']
        self.assertNotIn(student, form.fields['teacher'].queryset)

    def test_edit_and_delete(self):
        self.client.post(reverse('assignment_list'), self.payload())
        a = TeachingAssignment.objects.get()
        self.client.post(reverse('assignment_update', args=[a.pk]), self.payload(section=self.bsit2b.pk))
        a.refresh_from_db()
        self.assertEqual(a.section, self.bsit2b)
        self.client.post(reverse('assignment_delete', args=[a.pk]))
        self.assertEqual(TeachingAssignment.objects.count(), 0)

    def test_list_filters_by_semester(self):
        TeachingAssignment.objects.create(teacher=self.teacher_it, subject=self.web, section=self.bsit2a,
                                          school_year='2026-2027', semester='1st')
        TeachingAssignment.objects.create(teacher=self.teacher_it, subject=self.web, section=self.bsit2a,
                                          school_year='2026-2027', semester='2nd')
        response = self.client.get(reverse('assignment_list'))
        self.assertEqual(len(response.context['assignments']), 1)
        response = self.client.get(reverse('assignment_list') + '?semester=all')
        self.assertEqual(len(response.context['assignments']), 2)

    def test_no_evaluation_period_shows_warning_and_blocks_saving(self):
        EvaluationPeriod.objects.all().delete()
        response = self.client.get(reverse('assignment_list'))
        self.assertContains(response, 'No Evaluation Period exists yet')
        response = self.client.post(reverse('assignment_list'), self.payload())
        self.assertEqual(TeachingAssignment.objects.count(), 0)


class UserManagementTests(AcademicBase):

    def setUp(self):
        super().setUp()
        self.login_admin()

    def test_create_student_with_section_forces_password_change(self):
        response = self.client.post(reverse('create_student'), {
            'first_name': 'Ana', 'last_name': 'Cruz', 'email': 'ana@test.com',
            'program': 'it', 'section': self.bsit2a.pk, 'password': 'Temp-1234!',
        })
        self.assertEqual(response.status_code, 302)
        ana = User.objects.get(email='ana@test.com')
        self.assertEqual(ana.section, self.bsit2a)
        self.assertTrue(ana.must_change_password)
        self.assertEqual(ana.role, 'student')

    def test_student_section_must_match_program(self):
        response = self.client.post(reverse('create_student'), {
            'first_name': 'Ben', 'last_name': 'Lim', 'email': 'ben@test.com',
            'program': 'cs', 'section': self.bsit2a.pk, 'password': 'Temp-1234!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='ben@test.com').exists())

    def test_create_instructor_needs_department(self):
        data = {'first_name': 'Cy', 'last_name': 'Dee', 'email': 'cy@test.com', 'password': 'Temp-1234!'}
        self.client.post(reverse('create_instructor'), data)
        self.assertFalse(User.objects.filter(email='cy@test.com').exists())

        self.client.post(reverse('create_instructor'), {**data, 'department': self.gened.pk})
        cy = User.objects.get(email='cy@test.com')
        self.assertEqual(cy.department, self.gened)
        self.assertTrue(cy.must_change_password)

    def test_update_student_section(self):
        student = make_user('stud@test.com', 'student', program='it')
        self.client.post(reverse('update_student', args=[student.pk]), {
            'first_name': 'S', 'last_name': 'T', 'email': 'stud@test.com',
            'program': 'it', 'section': self.bsit2b.pk,
        })
        student.refresh_from_db()
        self.assertEqual(student.section, self.bsit2b)

    def test_activate_deactivate_student_and_instructor(self):
        student = make_user('stud@test.com', 'student')
        for user, off, on in [(student, 'deactivate_student', 'activate_student'),
                              (self.teacher_it, 'deactivate_instructor', 'activate_instructor')]:
            self.client.post(reverse(off, args=[user.pk]))
            user.refresh_from_db()
            self.assertFalse(user.is_active)
            self.client.post(reverse(on, args=[user.pk]))
            user.refresh_from_db()
            self.assertTrue(user.is_active)

    def test_deactivated_instructor_cannot_log_in(self):
        self.teacher_it.is_active = False
        self.teacher_it.save()
        self.client.logout()
        response = self.client.post(reverse('login'), {'username': 'itprof@test.com', 'password': PASSWORD})
        self.assertContains(response, 'deactivated')

    def test_student_list_delete_button_is_not_inside_status_form(self):
        make_user('stud@test.com', 'student')
        html = self.client.get(reverse('create_student')).content.decode()
        # every <form ...> must be closed before the next one opens
        self.assertEqual(html.count('<form'), html.count('</form>'))


class FirstLoginTests(AcademicBase):

    def test_student_is_forced_to_change_credentials_everywhere(self):
        make_user('stud@test.com', 'student', program='it')               # must_change_password=True
        self.client.post(reverse('login'), {'username': 'stud@test.com', 'password': PASSWORD})

        for name in ('student_dashboard', 'create_student', 'department_list'):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse('force_update'), fetch_redirect_response=False)

        self.assertEqual(self.client.get(reverse('force_update')).status_code, 200)

    def test_instructor_first_login_page_works_with_department_field_removed(self):
        self.teacher_it.must_change_password = True
        self.teacher_it.save()
        self.client.post(reverse('login'), {'username': 'itprof@test.com', 'password': PASSWORD})
        response = self.client.get(reverse('force_update'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('department', response.context['form'].fields)

        response = self.client.post(reverse('force_update'), {
            'first_name': 'It', 'middle_name': '', 'last_name': 'Prof', 'email': 'itprof@test.com',
            'password': 'Brand-new-9xK!', 'confirm_password': 'Brand-new-9xK!',
        })
        self.assertRedirects(response, reverse('instructor_dashboard'), fetch_redirect_response=False)
        self.teacher_it.refresh_from_db()
        self.assertFalse(self.teacher_it.must_change_password)
        self.assertEqual(self.teacher_it.department, self.it)              # department untouched

    def test_new_password_rules(self):
        make_user('stud@test.com', 'student', program='it')
        self.client.post(reverse('login'), {'username': 'stud@test.com', 'password': PASSWORD})
        base = {'first_name': 'S', 'middle_name': '', 'last_name': 'T', 'email': 'stud@test.com'}

        # same as the temporary password
        r = self.client.post(reverse('force_update'), {**base, 'password': PASSWORD, 'confirm_password': PASSWORD})
        self.assertContains(r, 'different from the temporary password')

        # too short / too common
        r = self.client.post(reverse('force_update'), {**base, 'password': 'abc', 'confirm_password': 'abc'})
        self.assertEqual(r.status_code, 200)

        # mismatch
        r = self.client.post(reverse('force_update'), {**base, 'password': 'Brand-new-9xK!', 'confirm_password': 'x'})
        self.assertContains(r, 'do not match')

        student = User.objects.get(email='stud@test.com')
        self.assertTrue(student.must_change_password)                      # still forced

        # good password -> free to go
        r = self.client.post(reverse('force_update'), {**base, 'password': 'Brand-new-9xK!', 'confirm_password': 'Brand-new-9xK!'})
        self.assertRedirects(r, reverse('student_dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('student_dashboard')).status_code, 200)

    def test_admin_is_never_forced(self):
        self.admin.must_change_password = True
        self.admin.save()
        self.login_admin()
        self.assertEqual(self.client.get(reverse('admin_dashboard')).status_code, 200)
