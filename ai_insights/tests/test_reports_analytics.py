"""Generate AI Reports (admin): sections, filters, forecast vs actual, privacy, files."""
import io

from django.http import QueryDict
from django.urls import reverse

from ai_insights.filters import parse_filters
from ai_insights.models import ReportLog
from ai_insights.services import reports

from .test_views import Base, COMMENTS

SECTIONS = ['Executive Summary', 'School Performance', 'Department Performance', 'Instructor Performance',
            'Evaluation Category Scores', 'Historical Trends', 'AI Forecasting Results', 'AI Student Feedback Summaries',
            'Strengths', 'Weaknesses', 'Performance Alerts', 'AI-Generated Recommendations']


def build(qs='', level='school'):
    return reports.analytics_report(parse_filters(QueryDict(qs)), level)


class ReportContentTests(Base):
    def headings(self, rep):
        return [s['heading'] for s in rep['sections']]

    def test_school_report_has_every_required_section(self):
        rep = build()
        for name in SECTIONS:
            self.assertTrue(any(h.startswith(name) for h in self.headings(rep)), name)

    def test_metadata_has_year_semester_period_and_date(self):
        meta = dict(build()['meta'])
        for key in ('Academic year', 'Semester', 'Evaluation period', 'Report generated'):
            self.assertTrue(meta[key])

    def test_actual_and_forecast_are_separate_columns(self):
        sec = next(s for s in build()['sections'] if s['heading'].startswith('AI Forecasting'))
        self.assertIn('Last real score', sec['headers'])
        self.assertIn('AI forecast', sec['headers'])

    def test_instructor_without_forecast_says_why(self):
        newbie = self.periods[1]
        rep = build(f'period={newbie.pk}', 'school')
        sec = next(s for s in rep['sections'] if s['heading'].startswith('AI Forecasting'))
        self.assertTrue(all(r[1] == '' and r[2] == '' and r[6] for r in sec['rows']))

    def test_no_comments_or_student_data_in_any_cell(self):
        rep = build(f'instructor={self.teacher.pk}', 'instructor')
        text = ' '.join(str(c) for s in rep['sections'] for r in s['rows'] for c in r) + rep['title']
        for secret in COMMENTS + ['stud@test.com']:
            self.assertNotIn(secret, text)

    def test_levels_follow_filters(self):
        self.assertIn('Whole school', build(f'instructor={self.teacher.pk}', 'school')['title'])
        self.assertIn('Information Technology', build(f'department={self.it.pk}', 'department')['title'])
        self.assertIn('Rivalson', build(f'instructor={self.rival.pk}', 'instructor')['title'])

    def test_department_level_only_has_that_department(self):
        rep = build(f'department={self.it.pk}', 'department')
        sec = next(s for s in rep['sections'] if s['heading'] == 'Instructor Performance')
        self.assertTrue(all(r[1] == 'Information Technology' for r in sec['rows']))

    def test_no_data_report_says_so(self):
        from evaluation.models import Evaluation
        Evaluation.objects.all().delete()
        rep = build()
        self.assertIn('nothing to report', rep['sections'][0]['text'])
        reports.to_pdf(rep)

    def test_files_are_made_with_charts_and_sheets(self):
        rep = build(f'instructor={self.teacher.pk}', 'instructor')
        self.assertTrue(any(s.get('charts') for s in rep['sections']))
        self.assertTrue(reports.to_pdf(rep).startswith(b'%PDF'))
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(reports.to_xlsx(rep)))
        self.assertGreaterEqual(len(book.worksheets), len(SECTIONS))


class ReportDownloadTests(Base):
    def post(self, **data):
        self.login(self.admin)
        return self.client.post(reverse('ai_admin_report_download'), data)

    def test_download_uses_posted_filters_and_is_logged(self):
        r = self.post(kind='instructor', fmt='pdf', instructor=self.rival.pk)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertIn('rivalson', r['Content-Disposition'].lower())
        self.assertEqual(ReportLog.objects.count(), 1)

    def test_instructor_level_needs_an_instructor(self):
        self.assertEqual(self.post(kind='instructor', fmt='pdf').status_code, 302)

    def test_excel_download(self):
        r = self.post(kind='school', fmt='xlsx')
        self.assertTrue(r.content.startswith(b'PK'))

    def test_page_shows_filters_and_period(self):
        self.login(self.admin)
        r = self.client.get(reverse('ai_admin_reports') + f'?period={self.periods[2].pk}')
        self.assertContains(r, 'Report level')
        self.assertEqual(r.context['period'], self.periods[2])

    def test_only_admin(self):
        for user in (self.teacher, self.student):
            self.login(user)
            self.assertEqual(self.client.get(reverse('ai_admin_reports')).status_code, 403)
            self.assertEqual(self.client.post(reverse('ai_admin_report_download'), {'kind': 'school', 'fmt': 'pdf'}).status_code, 403)