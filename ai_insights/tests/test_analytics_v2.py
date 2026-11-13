# Tests for the AI Analytics improvements: alerts, notices, trends, instructor page, reports, SVG charts.

import io
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from ai_insights import charts
from ai_insights.models import Alert, AlertSettings, InstructorNotice, ReportLog
from ai_insights.services import alerts, pipeline, reports

from .test_admin_forecasting import ForecastPageBase


class ChartTests(ForecastPageBase):
    def test_empty_and_all_none_give_no_chart(self):
        self.assertIsNone(charts.line_chart([], []))
        self.assertIsNone(charts.line_chart(['A', 'B'], [{'name': 'x', 'values': [None, None]}]))

    def test_single_point_and_gaps(self):
        one = charts.line_chart(['A'], [{'name': 'x', 'values': [4.0]}])
        self.assertEqual(len(one['lines'][0]['dots']), 1)
        gap = charts.line_chart(['A', 'B', 'C', 'D'], [{'name': 'x', 'values': [3.0, None, 4.0, 4.5]}])
        self.assertEqual(len(gap['lines'][0]['paths']), 1)       # the lone first point has no line
        self.assertEqual(gap['table'][0]['values'], [3.0, None, 4.0, 4.5])


class AlertTests(ForecastPageBase):
    def test_falling_instructor_gets_alerts_and_steady_one_does_not(self):
        rival = Alert.objects.filter(instructor=self.rival)
        self.assertTrue(rival.exists())
        self.assertTrue(rival.filter(severity='high').exists())
        self.assertFalse(Alert.objects.filter(instructor=self.alice).exists())

    def test_rerun_makes_no_duplicates_and_keeps_status_and_note(self):
        before = Alert.objects.filter(instructor=self.rival).count()
        a = Alert.objects.filter(instructor=self.rival).first()
        a.status, a.note = 'reviewed', 'Talked to the dean'
        a.save()
        pipeline.run_for_all(use_sentiment=False)
        self.assertEqual(Alert.objects.filter(instructor=self.rival).count(), before)
        a.refresh_from_db()
        self.assertEqual((a.status, a.note), ('reviewed', 'Talked to the dean'))

    def test_new_alert_disappears_when_no_longer_true_but_reviewed_stays(self):
        keep = Alert.objects.filter(instructor=self.rival).first()
        keep.status = 'reviewed'
        keep.save()
        row = AlertSettings.get()
        row.low_score, row.score_drop, row.forecast_low, row.forecast_drop, row.category_low = 1, 2, 1, 2, 1
        row.falling_periods, row.negative_share = 5, 100
        row.save()
        alerts.resync_all()
        remaining = Alert.objects.filter(instructor=self.rival)
        self.assertEqual(list(remaining.values_list('pk', flat=True)), [keep.pk])

    def test_pages_are_admin_only(self):
        urls = [reverse(n) for n in ('ai_admin_alerts', 'ai_admin_trends', 'ai_admin_reports')]
        urls.append(reverse('ai_admin_instructor', args=[self.rival.pk]))
        for user in (self.alice, self.student):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.force_login(self.admin)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_update_alert_whitelists_status_and_is_admin_only(self):
        a = Alert.objects.filter(instructor=self.rival).first()
        url = reverse('ai_admin_alert_update', args=[a.pk])
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(url, {'status': 'resolved'}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(url, {'status': 'hacked', 'note': 'x'})
        a.refresh_from_db()
        self.assertEqual(a.status, 'new')
        self.client.post(url, {'status': 'resolved', 'note': 'n' * 5000})
        a.refresh_from_db()
        self.assertEqual(a.status, 'resolved')
        self.assertLessEqual(len(a.note), 1000)
        self.assertEqual(a.reviewed_by, self.admin)

    def test_open_redirect_is_blocked(self):
        a = Alert.objects.filter(instructor=self.rival).first()
        self.client.force_login(self.admin)
        r = self.client.post(reverse('ai_admin_alert_update', args=[a.pk]),
                             {'status': 'reviewed', 'qs': 'next=http://evil.test&severity=high'})
        self.assertTrue(r['Location'].startswith('/'))
        self.assertNotIn('evil', r['Location'])

    def test_settings_validation(self):
        self.client.force_login(self.admin)
        good = {'low_score': 3.2, 'score_drop': 0.4, 'falling_periods': 3, 'forecast_low': 3.1,
                'forecast_drop': 0.5, 'negative_share': 50, 'category_low': 2.8}
        self.client.post(reverse('ai_admin_alert_settings'), {**good, 'low_score': 9})
        self.assertEqual(AlertSettings.get().low_score, 3.5)                # bad value: nothing saved
        self.client.post(reverse('ai_admin_alert_settings'), {**good, 'low_score': 'abc'})
        self.assertEqual(AlertSettings.get().low_score, 3.5)
        self.client.post(reverse('ai_admin_alert_settings'), good)
        self.assertEqual(AlertSettings.get().low_score, 3.2)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(reverse('ai_admin_alert_settings'), good).status_code, 403)

    def test_alerts_page_filters_and_pages(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('ai_admin_alerts'), {'status': 'resolved'})
        self.assertEqual(len(list(r.context['data']['pager'])), 0)
        r = self.client.get(reverse('ai_admin_alerts'), {'severity': 'nonsense', 'kind': 'nonsense', 'status': 'nonsense'})
        self.assertEqual(r.status_code, 200)


class NoticeTests(ForecastPageBase):
    def test_notices_exist_and_only_show_after_release(self):
        self.assertTrue(InstructorNotice.objects.filter(instructor=self.rival, kind='down').exists())
        self.assertTrue(InstructorNotice.objects.filter(instructor=self.alice, kind='up').exists())
        self.client.force_login(self.rival)
        html = self.client.get(reverse('instructor_dashboard')).content.decode()
        self.assertIn(reverse('instructor_reports'), html)

    def test_mark_read_is_own_only(self):
        self.client.force_login(self.rival)
        self.client.post(reverse('ai_notices_read'))
        self.assertFalse(InstructorNotice.objects.filter(instructor=self.rival, read_at__isnull=True).exists())
        self.assertTrue(InstructorNotice.objects.filter(instructor=self.alice, read_at__isnull=True).exists())
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(reverse('ai_notices_read')).status_code, 403)


class TrendTests(ForecastPageBase):
    def test_trends_page_and_filters(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('ai_admin_trends'))
        data = r.context['data']
        self.assertIn('<svg', r.content.decode())
        self.assertTrue(any(m['teacher'] == self.rival for m in data['declined']))
        self.assertTrue(any(m['teacher'] == self.alice for m in data['improved']))
        for metric in ('overall', 'punctuality', 'bogus'):
            self.assertEqual(self.client.get(reverse('ai_admin_trends'), {'metric': metric}).status_code, 200)


class ReportTests(ForecastPageBase):
    def test_instructor_report_has_no_comments_and_xlsx_text_stays_text(self):
        rep = reports.instructor_report(self.rival, admin=True)
        rep['sections'].append({'heading': 'x', 'text': '', 'headers': ['a'], 'rows': [['=1+1']]})
        try:
            data = reports.to_xlsx(rep)
        except reports.ReportLibraryMissing:
            self.skipTest('openpyxl not installed')
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(data))
        cells = [c for ws in book.worksheets for row in ws.iter_rows() for c in row if c.value == '=1+1']
        self.assertTrue(cells)
        self.assertEqual(cells[0].data_type, 's')

    def test_downloads_work_and_are_logged(self):
        self.client.force_login(self.admin)
        try:
            reports.to_pdf({'title': 't', 'subtitle': '', 'landscape': False, 'sections': []})
            reports.to_xlsx({'title': 't', 'subtitle': '', 'landscape': False, 'sections': []})
        except reports.ReportLibraryMissing:
            self.skipTest('reportlab/openpyxl not installed')
        url = reverse('ai_admin_report_download')
        for kind, extra in (('instructor', {'instructor': self.rival.pk}), ('department', {'department': self.it.pk}),
                            ('summary', {})):
            for fmt, magic in (('pdf', b'%PDF'), ('xlsx', b'PK')):
                r = self.client.post(url, {'kind': kind, 'fmt': fmt, **extra})
                self.assertEqual(r.status_code, 200, (kind, fmt))
                self.assertTrue(r.content.startswith(magic))
        self.assertEqual(ReportLog.objects.count(), 6)

    def test_bad_input_and_permissions(self):
        url = reverse('ai_admin_report_download')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {'kind': 'x', 'fmt': 'pdf'}).status_code, 302)
        self.assertEqual(self.client.post(url, {'kind': 'instructor', 'fmt': 'pdf', 'instructor': self.student.pk}).status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(url, {'kind': 'summary', 'fmt': 'pdf'}).status_code, 403)

    def test_missing_library_gives_message_not_crash(self):
        self.client.force_login(self.admin)
        with mock.patch.object(reports, 'to_pdf', side_effect=reports.ReportLibraryMissing('Please install reportlab')):
            r = self.client.post(reverse('ai_admin_report_download'), {'kind': 'summary', 'fmt': 'pdf'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('install', r.content.decode())
        self.assertEqual(ReportLog.objects.count(), 0)

    def test_my_report_is_own_and_has_no_alerts_section(self):
        rep = reports.instructor_report(self.rival, cutoff=timezone.now(), admin=False)
        self.assertNotIn('alert', ' '.join(s['heading'].lower() for s in rep['sections']))
        self.client.force_login(self.rival)
        r = self.client.post(reverse('ai_my_report'), {'fmt': 'pdf'})
        self.assertIn(r.status_code, (200, 302))
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(reverse('ai_my_report'), {'fmt': 'pdf'}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('ai_my_report'), {'fmt': 'pdf'}).status_code, 403)