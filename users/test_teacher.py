# users/test_teacher.py
#
# Tests for the TEACHER pages: dashboard, results, QR page, profile.
# Run with:   python manage.py test users.test_teacher
# (A temporary database is used, so your real data is not touched.)

import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from evaluation.models import Evaluation, EvaluationPeriod, QRCode
from users.models import User
from users import views as user_views

TODAY = timezone.now().date()
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
       b'\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\x9f'
       b'\x1b\x00\x00\x00\x00IEND\xaeB`\x82')


def make_user(email, role, **extra):
    user = User(email=email, username=email, role=role, first_name=email.split('@')[0].title(),
                last_name='Test', must_change_password=False, **extra)
    user.set_password('Pass-12345!')
    user.save()
    return user


class TeacherBase(TestCase):
    counter = 0

    def setUp(self):
        self.teacher = make_user('teacher@test.com', 'instructor')
        self.other = make_user('other@test.com', 'instructor')
        self.admin = make_user('admin@test.com', 'admin')
        self.student = make_user('stud@test.com', 'student')

    # -- helpers --------------------------------------------------------
    def period(self, term='prelims', semester='1st', year='2025-2026',
               start=-60, end=-30, active=False):
        return EvaluationPeriod.objects.create(
            school_year=year, semester=semester, term=term,
            start_date=TODAY + datetime.timedelta(days=start),
            end_date=TODAY + datetime.timedelta(days=end),
            is_active=active,
        )

    def rate(self, period, teacher, scores, comments=None):
        """Create one evaluation per score (each from a NEW student). scores are 1-5."""
        was_active = period.is_active
        EvaluationPeriod.objects.filter(pk=period.pk).update(is_active=True)   # model needs an open period
        period.refresh_from_db()
        for i, score in enumerate(scores):
            TeacherBase.counter += 1
            s = make_user(f'student{TeacherBase.counter}@secret-school.com', 'student')
            s.first_name = f'Secretname{TeacherBase.counter}'
            s.save()
            Evaluation.objects.create(
                student=s, teacher=teacher, period=period,
                teaching_effectiveness=score, communication_skills=score,
                classroom_management=score, punctuality=score, student_engagement=score,
                comment=(comments[i] if comments else ''),
            )
        EvaluationPeriod.objects.filter(pk=period.pk).update(is_active=was_active)
        period.refresh_from_db()

    def login(self, user):
        self.client.force_login(user)


class AccessTests(TeacherBase):

    def test_visitors_are_sent_to_login(self):
        for name in ('instructor_dashboard', 'instructor_evaluation_results', 'instructor_qr', 'instructor_reports'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)

    def test_students_and_admins_cannot_open_teacher_pages(self):
        for user in (self.student, self.admin):
            self.login(user)
            self.assertEqual(self.client.get(reverse('instructor_qr')).status_code, 403)
            self.assertEqual(self.client.get(reverse('instructor_evaluation_results')).status_code, 403)
            self.assertEqual(self.client.get(reverse('instructor_dashboard')).status_code, 302)

    def test_teacher_pages_open(self):
        self.login(self.teacher)
        for name in ('instructor_dashboard', 'instructor_evaluation_results', 'instructor_qr', 'instructor_reports'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_pages_are_not_cached(self):
        self.login(self.teacher)
        for name in ('instructor_dashboard', 'instructor_evaluation_results', 'instructor_qr', 'instructor_reports'):
            self.assertIn('no-store', self.client.get(reverse(name))['Cache-Control'], name)


class PrivacyTests(TeacherBase):

    def test_teacher_only_sees_own_data_and_no_student_identity(self):
        old = self.period()
        self.rate(old, self.teacher, [5, 4, 5], ['Great class', 'Fun', 'Clear'])
        self.rate(old, self.other, [1, 1, 1], ['SECRET-OTHER-TEACHER-COMMENT', 'x', 'y'])
        self.login(self.teacher)

        pages = [
            self.client.get(reverse('instructor_dashboard')),
            self.client.get(reverse('instructor_evaluation_results')),
        ]
        for response in pages:
            html = response.content.decode()
            self.assertNotIn('Secretname', html)                 # no student first names
            self.assertNotIn('secret-school.com', html)          # no student emails
            self.assertNotIn('SECRET-OTHER-TEACHER-COMMENT', html)

        results = pages[1]
        self.assertEqual(results.context['snap']['responses'], 3)       # only my 3, not 6
        self.assertAlmostEqual(results.context['snap']['overall'], 4.67, places=2)

    def test_feedback_hidden_until_enough_students(self):
        old = self.period()
        self.rate(old, self.teacher, [5, 5], ['one', 'two'])            # only 2 students
        self.login(self.teacher)
        response = self.client.get(reverse('instructor_evaluation_results') + '?tab=comments')
        self.assertTrue(response.context['feedback']['hidden'])
        self.assertNotContains(response, 'one</li>')
        self.assertContains(response, 'stay hidden until at least 3')

    def test_feedback_shown_without_names_or_dates_and_is_escaped(self):
        old = self.period()
        self.rate(old, self.teacher, [5, 4, 3], ['<script>alert(1)</script>', 'Good', ''])
        self.login(self.teacher)
        response = self.client.get(reverse('instructor_evaluation_results') + '?tab=comments')
        fb = response.context['feedback']
        self.assertFalse(fb['hidden'])
        self.assertEqual(sorted(fb['comments']), ['<script>alert(1)</script>', 'Good'])
        self.assertEqual(fb['count'], 2)
        self.assertEqual(fb['total'], 3)
        html = response.content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)             # escaped, not run
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)

    def test_feedback_never_comes_from_an_open_period(self):
        live = self.period(term='midterm', start=-3, end=+10, active=True)
        self.rate(live, self.teacher, [5, 5, 5], ['live comment'] * 3)
        self.login(self.teacher)
        html = self.client.get(reverse('instructor_evaluation_results') +
                               f'?school_year={live.school_year}&semester={live.semester}&term={live.term}&tab=comments').content.decode()
        self.assertNotIn('live comment', html)
        self.assertIn('Comments are shown after the period closes', html)


class ResultsStateTests(TeacherBase):

    def url(self, p):
        return (reverse('instructor_evaluation_results') +
                f'?school_year={p.school_year}&semester={p.semester}&term={p.term}')

    def test_states(self):
        self.login(self.teacher)
        done = self.period(term='prelims'); self.rate(done, self.teacher, [4, 4, 4])
        closed_by_admin = self.period(term='midterm', start=-20, end=+5, active=False)   # closed early
        self.rate(closed_by_admin, self.teacher, [3, 3, 3])
        upcoming = self.period(term='finals', start=+10, end=+20, active=False)
        empty = self.period(term='prefinals', start=-90, end=-80)

        self.assertEqual(self.client.get(self.url(done)).context['state'], 'finished')
        self.assertEqual(self.client.get(self.url(closed_by_admin)).context['state'], 'finished')
        self.assertEqual(self.client.get(self.url(upcoming)).context['state'], 'not_open')
        self.assertEqual(self.client.get(self.url(empty)).context['state'], 'no_responses')
        self.assertEqual(self.client.get(
            reverse('instructor_evaluation_results') + '?school_year=1999&semester=1st&term=prelims'
        ).context['state'], 'not_found')

    def test_open_period_shows_live_scores_marked_not_final(self):
        live = self.period(term='midterm', start=-3, end=+10, active=True)
        self.rate(live, self.teacher, [5, 5, 5])
        self.login(self.teacher)
        response = self.client.get(self.url(live))
        self.assertEqual(response.context['state'], 'open')
        self.assertContains(response, 'Not final')

    def test_period_that_ended_is_treated_as_finished_even_if_still_marked_open(self):
        stale = self.period(term='midterm', start=-20, end=-1, active=True)    # deadline passed yesterday
        self.rate(stale, self.teacher, [4, 4, 4])
        EvaluationPeriod.objects.filter(pk=stale.pk).update(is_active=True)
        self.login(self.teacher)
        self.assertEqual(self.client.get(self.url(stale)).context['state'], 'finished')
        stale.refresh_from_db()
        self.assertFalse(stale.is_active)                                       # closed automatically

    def test_low_sample_notice(self):
        p = self.period(); self.rate(p, self.teacher, [5, 5])
        self.login(self.teacher)
        response = self.client.get(self.url(p))
        self.assertTrue(response.context['snap']['low_sample'])
        self.assertContains(response, 'Only 2 students answered')

        p2 = self.period(term='midterm', start=-25, end=-10); self.rate(p2, self.teacher, [5, 5, 5])
        self.assertFalse(self.client.get(self.url(p2)).context['snap']['low_sample'])

    def test_comparison_with_previous_period(self):
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [3, 3, 3])
        b = self.period(term='midterm', start=-40, end=-30); self.rate(b, self.teacher, [4, 4, 4])
        self.login(self.teacher)
        response = self.client.get(self.url(b))
        self.assertEqual(response.context['snap']['change'], 1.0)
        self.assertEqual(response.context['snap']['indicator'], 'improving')


class DashboardTests(TeacherBase):

    def get(self):
        self.login(self.teacher)
        return self.client.get(reverse('instructor_dashboard'))

    def test_dashboard_closes_expired_period(self):
        stale = self.period(start=-20, end=-1, active=True)
        response = self.get()
        self.assertIsNone(response.context['open_period'])
        stale.refresh_from_db()
        self.assertFalse(stale.is_active)

    def test_open_period_is_shown_live_and_compared_with_the_last_finished_one(self):
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [3, 3, 3])
        live = self.period(term='midterm', start=-3, end=+10, active=True)
        self.rate(live, self.teacher, [5, 5, 5])
        response = self.get()
        snap = response.context['snap']
        self.assertEqual(response.context['state'], 'open')
        self.assertEqual((snap['overall'], snap['previous']['overall'], snap['change']), (5.0, 3.0, 2.0))
        self.assertEqual(snap['indicator'], 'improving')
        self.assertContains(response, 'Evaluation open')
        self.assertContains(response, 'Not final')
        self.assertContains(response, 'Now, so far (not final)')            # dashed point in the line chart

    def test_no_open_period_shows_the_latest_finished_period(self):
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [4, 4, 4])
        b = self.period(term='midterm', start=-40, end=-30); self.rate(b, self.teacher, [3, 3, 3])
        response = self.get()
        self.assertEqual(response.context['state'], 'latest')
        self.assertEqual(response.context['snap']['period'], b)
        self.assertEqual(response.context['snap']['indicator'], 'declining')
        self.assertContains(response, 'No evaluation open')

    def test_open_period_the_teacher_is_not_part_of_falls_back_to_latest(self):
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [4, 4, 4])
        self.period(term='midterm', start=-3, end=+10, active=True)         # no answers, no sections for this teacher
        response = self.get()
        self.assertEqual(response.context['state'], 'latest')
        self.assertEqual(response.context['snap']['period'], a)

    def test_nothing_yet_shows_an_empty_message(self):
        p = self.period(); self.rate(p, self.other, [5, 5, 5])
        response = self.get()
        self.assertEqual(response.context['state'], 'empty')
        self.assertContains(response, 'No evaluation results yet')

    def test_responses_out_of_total_students_in_my_sections(self):
        from academics.models import Department, Section, TeachingAssignment, Subject
        dept = Department.objects.filter(name='Information Technology').first() or Department.objects.create(code='IT', name='Information Technology')
        sec = Section.objects.create(name='BSIT 1A', program='BSIT', department=dept)
        subj = Subject.objects.create(code='IT101', name='Intro', department=dept)
        TeachingAssignment.objects.create(teacher=self.teacher, subject=subj, section=sec,
                                          school_year='2025-2026', semester='1st')
        for i in range(10):
            make_user(f'sec{i}@x.com', 'student', section=sec)
        make_user('inactive@x.com', 'student', section=sec, is_active=False)     # does not count
        p = self.period(term='prelims', start=-60, end=-50); self.rate(p, self.teacher, [4, 4, 4, 4])
        snap = self.get().context['snap']
        self.assertEqual((snap['responses'], snap['expected'], snap['percent']), (4, 10, 40))

    def test_strongest_weakest_and_needs_attention(self):
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [4, 4, 4])
        b = self.period(term='midterm', start=-40, end=-30)
        self.rate(b, self.teacher, [5, 5, 5])
        # make punctuality low and engagement drop sharply in period b
        Evaluation.objects.filter(teacher=self.teacher, period=b).update(punctuality=3, student_engagement=3)
        snap = self.get().context['snap']
        self.assertEqual(snap['strongest']['score'], 5.0)
        self.assertEqual(snap['weakest']['score'], 3.0)
        self.assertEqual({r['key'] for r in snap['attention']}, {'punctuality', 'student_engagement'})
        self.assertEqual(snap['attention'][0]['score'], 3.0)

    def test_everything_is_in_the_page(self):
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [4, 4, 4])
        html = self.get().content.decode()
        for text in ('Responses received', 'Average score', 'Compared with the previous period', 'Strongest area',
                     'Weakest area', 'Needs attention', 'Progress over time', 'Ratings by area',
                     'Feedback summary', 'Suggestions', 'Forecast', 'Updates'):
            self.assertIn(text, html)

    def test_forecast_progress_only_counts_real_data(self):
        ai = self.get().context['ai']
        self.assertEqual((ai['progress']['have'], ai['progress']['need']), (0, 4))
        for i, term in enumerate(('prelims', 'midterm')):
            p = self.period(term=term, start=-100 + i * 20, end=-90 + i * 20); self.rate(p, self.teacher, [4, 4, 4])
        response = self.get()
        self.assertEqual(response.context['ai']['progress']['have'], 2)
        self.assertContains(self.client.get(reverse('instructor_dashboard'), {'section': 'forecast'}), 'You have 2 of 4 finished periods with enough responses')

    def test_progress_ignores_periods_with_too_few_responses(self):
        for i, term in enumerate(('prelims', 'midterm', 'prefinals', 'finals')):
            p = self.period(term=term, start=-100 + i * 20, end=-90 + i * 20); self.rate(p, self.teacher, [4, 4])
        self.assertEqual(self.get().context['ai']['progress']['have'], 0)

    def test_other_teachers_periods_do_not_count(self):
        p = self.period(); self.rate(p, self.other, [5, 5, 5])
        self.assertEqual(self.get().context['ai']['progress']['have'], 0)

    def test_updates_come_from_real_data(self):
        titles = [n['title'] for n in self.get().context['notifications']]
        self.assertEqual(titles, ['Your QR code is not ready'])             # nothing else is true yet

        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [3, 3, 3])
        b = self.period(term='midterm', start=-40, end=-30); self.rate(b, self.teacher, [4, 4, 4])
        self.period(term='finals', start=-2, end=+5, active=True)
        notes = self.client.get(reverse('instructor_dashboard')).context['notifications']
        titles = [n['title'] for n in notes]
        self.assertIn('An evaluation is open', titles)
        self.assertIn('Latest final results', titles)
        self.assertTrue(any('went up by 1.00' in n['text'] for n in notes))

    def test_low_sample_warning_on_live_score(self):
        live = self.period(term='midterm', start=-3, end=+10, active=True)
        self.rate(live, self.teacher, [5])
        response = self.get()
        self.assertTrue(response.context['snap']['low_sample'])
        self.assertContains(response, 'Only 1 student answered so far')

    def test_profile_view_still_works(self):
        self.login(self.teacher)
        response = self.client.get(reverse('instructor_dashboard') + '?tab=profile')
        self.assertEqual(response.context['active_tab'], 'profile')
        self.assertContains(response, 'id="profileForm"')


class ResultsPageTests(TeacherBase):
    """Filters, complete history, AI tab, comments and pagination of Evaluation Results."""

    def setUp(self):
        super().setUp()
        self.periods = []
        for i in range(10):                      # 10 finished periods = 2 pages of 8
            year = f'20{20 + i // 4}-20{21 + i // 4}'
            term = ('prelims', 'midterm', 'prefinals', 'finals')[i % 4]
            p = self.period(term=term, year=year, start=-400 + i * 30, end=-390 + i * 30)
            self.rate(p, self.teacher, [3 + (i % 3)] * 3, ['good teacher', 'nice class', 'ok'])
            self.periods.append(p)
        self.login(self.teacher)
        self.url = reverse('instructor_evaluation_results')

    def test_history_has_every_period_and_is_paginated(self):
        response = self.client.get(self.url + '?tab=history')
        self.assertEqual(response.context['history_total'], 10)
        self.assertEqual(len(response.context['pager']), 8)
        page2 = self.client.get(self.url + '?tab=history&page=2')
        self.assertEqual(len(page2.context['pager']), 2)
        self.assertContains(page2, 'Showing 9–10 of 10')
        self.assertIsNotNone(response.context['history_chart'])

    def test_filter_picks_the_period(self):
        p = self.periods[3]
        response = self.client.get(self.url + f'?school_year={p.school_year}&semester={p.semester}&term={p.term}')
        self.assertEqual(response.context['snap']['period'], p)

    def test_overview_uses_the_same_numbers_as_the_dashboard(self):
        dash = self.client.get(reverse('instructor_dashboard')).context['snap']
        res = self.client.get(self.url).context['snap']
        for key in ('overall', 'responses', 'change', 'indicator', 'label'):
            self.assertEqual(dash[key], res[key])

    def test_comments_are_paginated_and_anonymous(self):
        response = self.client.get(self.url + '?tab=comments')
        self.assertEqual(response.context['feedback']['count'], 3)
        self.assertContains(response, 'anonymous comment')
        self.assertNotContains(response, 'Secretname')

    def test_ai_tab_loads_for_a_finished_period(self):
        response = self.client.get(self.url + '?tab=ai&section=forecast')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No forecast yet')                   # no official AI analysis in this test

    def test_unknown_tab_falls_back_to_overview(self):
        self.assertEqual(self.client.get(self.url + '?tab=hack').context['tab'], 'overview')


class ReportsPageTests(TeacherBase):

    def setUp(self):
        super().setUp()
        a = self.period(term='prelims', start=-60, end=-50); self.rate(a, self.teacher, [3, 3, 3], ['fine', 'ok', 'good'])
        b = self.period(term='midterm', start=-40, end=-30); self.rate(b, self.teacher, [4, 4, 4], ['great', 'nice', 'clear'])
        self.a, self.b = a, b
        self.login(self.teacher)

    def test_menu_has_reports_and_no_ai_insights(self):
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertIn(reverse('instructor_reports'), html)
        self.assertNotIn('AI Insights', html)

    def test_page_lists_finished_periods_newest_first(self):
        response = self.client.get(reverse('instructor_reports'))
        self.assertEqual([o['id'] for o in response.context['options']], [self.b.pk, self.a.pk])
        self.assertContains(response, 'Download PDF')
        self.assertContains(response, 'Download Excel')

    def test_only_instructors_open_it(self):
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('instructor_reports')).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('instructor_reports')).status_code, 403)

    def test_pdf_and_excel_download_for_a_chosen_period(self):
        pdf = self.client.post(reverse('ai_my_report'), {'fmt': 'pdf', 'period': self.a.pk})
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        xlsx = self.client.post(reverse('ai_my_report'), {'fmt': 'xlsx'})
        self.assertTrue(xlsx.content.startswith(b'PK'))
        page = self.client.get(reverse('instructor_reports'))
        self.assertEqual(page.context['pager'].paginator.count, 2)         # both downloads are listed

    def test_report_is_only_about_my_data_and_has_no_comments(self):
        from ai_insights.services import reports
        self.rate(self.b, self.other, [1, 1, 1], ['SECRET-OTHER'] * 3)
        rep = reports.instructor_report(self.teacher, admin=False, period=self.b)
        text = str(rep)
        self.assertNotIn('SECRET-OTHER', text)
        self.assertNotIn('great', text)                                    # single comments never appear
        headings = [x['heading'] for x in rep['sections']]
        for h in ('Summary', 'Category scores', 'Real scores by period', 'AI forecast', 'Recommendations', 'Data notes'):
            self.assertIn(h, headings)
        self.assertNotIn('Alerts', headings)
        self.assertTrue(any(x.get('charts') for x in rep['sections']))     # charts are part of the report

    def test_report_for_period_chosen_by_someone_elses_id_is_refused(self):
        other_period = self.period(term='finals', start=-20, end=-10); self.rate(other_period, self.other, [5, 5, 5])
        response = self.client.post(reverse('ai_my_report'), {'fmt': 'pdf', 'period': other_period.pk})
        self.assertRedirects(response, reverse('instructor_reports'), fetch_redirect_response=False)

    def test_bad_format_is_refused_and_students_cannot_download(self):
        self.assertEqual(self.client.post(reverse('ai_my_report'), {'fmt': 'exe'}).status_code, 302)
        self.login(self.student)
        self.assertEqual(self.client.post(reverse('ai_my_report'), {'fmt': 'pdf'}).status_code, 403)


class QRPageTests(TeacherBase):

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)

    def test_no_qr_yet(self):
        self.login(self.teacher)
        response = self.client.get(reverse('instructor_qr'))
        self.assertIsNone(response.context['qr'])
        self.assertContains(response, 'not ready yet')

    def test_teacher_sees_only_own_qr(self):
        with override_settings(MEDIA_ROOT=self.media):
            mine = QRCode.objects.create(instructor=self.teacher, qr_image=SimpleUploadedFile('mine.png', PNG))
            QRCode.objects.create(instructor=self.other, qr_image=SimpleUploadedFile('theirs.png', PNG))
            self.login(self.teacher)
            response = self.client.get(reverse('instructor_qr'))
            self.assertEqual(response.context['qr'], mine)
            html = response.content.decode()
            self.assertIn('mine', html)
            self.assertNotIn('theirs', html)

    def test_missing_image_file_is_explained_not_a_crash(self):
        with override_settings(MEDIA_ROOT=self.media):
            qr = QRCode.objects.create(instructor=self.teacher, qr_image=SimpleUploadedFile('gone.png', PNG))
            qr.qr_image.delete(save=False)
            qr.qr_image.name = 'qr_codes/gone.png'
            qr.save()
            self.login(self.teacher)
            response = self.client.get(reverse('instructor_qr'))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['qr_missing_file'])
            self.assertContains(response, 'could not be found')


class ProfileTests(TeacherBase):

    def post(self, **data):
        base = {'first_name': 'Maria', 'last_name': 'Santos', 'email': 'teacher@test.com'}
        base.update(data)
        return self.client.post(reverse('faculty_update_profile'), base, follow=True)

    def test_valid_update_and_message_is_visible_on_profile_view(self):
        self.login(self.teacher)
        response = self.post(first_name='Maria', email='maria@test.com')
        self.teacher.refresh_from_db()
        self.assertEqual((self.teacher.first_name, self.teacher.email), ('Maria', 'maria@test.com'))
        self.assertEqual(response.redirect_chain[-1][0], reverse('instructor_dashboard') + '?tab=profile')
        self.assertEqual(response.context['active_tab'], 'profile')
        self.assertContains(response, 'Profile updated successfully')

    def test_email_used_by_someone_else_is_rejected(self):
        self.login(self.teacher)
        response = self.post(email='OTHER@test.com')                        # same address, other letter case
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, 'teacher@test.com')
        self.assertContains(response, 'already used by another account')

    def test_empty_or_missing_fields_do_not_crash(self):
        self.login(self.teacher)
        response = self.post(first_name='   ')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First name is required')
        response = self.client.post(reverse('faculty_update_profile'), {})  # nothing sent at all
        self.assertEqual(response.status_code, 302)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.first_name, 'Teacher')

    def test_other_roles_cannot_use_the_teacher_profile_endpoint(self):
        self.login(self.student)
        self.client.post(reverse('faculty_update_profile'),
                         {'first_name': 'Hacked', 'last_name': 'X', 'email': 'stud@test.com'})
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Stud')

    def test_department_cannot_be_changed_by_teacher(self):
        from academics.models import Department
        dept, _ = Department.objects.get_or_create(code='IT', defaults={'name': 'Information Technology'})
        other, _ = Department.objects.get_or_create(code='GENED', defaults={'name': 'General Education'})
        self.teacher.department = dept; self.teacher.save()
        self.login(self.teacher)
        self.post(department=other.pk, role='admin')
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.department, dept)
        self.assertEqual(self.teacher.role, 'instructor')