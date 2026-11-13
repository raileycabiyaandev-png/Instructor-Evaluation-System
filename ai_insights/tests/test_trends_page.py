"""AI Performance Trends page (admin)."""
from django.urls import reverse

from .test_views import Base, COMMENTS


class TrendsPageTests(Base):
    def get(self, qs=''):
        self.login(self.admin)
        return self.client.get(reverse('ai_admin_trends') + qs)

    def test_admin_only(self):
        for user in (self.teacher, self.student):
            self.login(user)
            self.assertEqual(self.client.get(reverse('ai_admin_trends')).status_code, 403)

    def test_table_has_five_categories_and_radar(self):
        r = self.get()
        html = r.content.decode()
        for label in ('Teaching Effectiveness', 'Communication Skills', 'Classroom Management', 'Punctuality', 'Student Engagement'):
            self.assertIn(label, html)
        self.assertIn('ai-svg--radar', html)
        self.assertGreaterEqual(r.context['data']['row_count'], 1)

    def test_period_and_instructor_filters(self):
        r = self.get(f'?period={self.periods[3].pk}&instructor={self.teacher.pk}')
        d = r.context['data']
        self.assertEqual(d['shown'], self.periods[3])
        self.assertEqual(d['row_count'], 1)
        self.assertContains(r, 'Forecast detail')

    def test_short_history_says_no_forecast(self):
        r = self.get(f'?period={self.periods[1].pk}')
        self.assertFalse(r.context['data']['forecast_ready'])
        self.assertContains(r, 'No forecast: needs')

    def test_no_comments_or_student_data(self):
        html = self.get().content.decode()
        for secret in COMMENTS + ['stud@test.com']:
            self.assertNotIn(secret, html)

    def test_bad_filter_values_ignored(self):
        self.assertEqual(self.get('?period=99999&instructor=abc&department=-1').status_code, 200)


class TrendsExtrasTests(TrendsPageTests.__bases__[0]):
    def get(self, qs=''):
        self.login(self.admin)
        return self.client.get(reverse('ai_admin_trends') + qs)

    def test_view_complete_details_button(self):
        r = self.get()
        self.assertContains(r, 'View details')
        self.assertContains(r, reverse('ai_admin_instructor', args=[self.teacher.pk]))

    def test_instructor_feedback_and_suggestions_tab(self):
        r = self.get(f'?instructor={self.teacher.pk}')
        self.assertContains(r, 'Feedback &amp; suggestions')
        self.assertIsNotNone(r.context['one'])
        for c in COMMENTS:
            self.assertNotContains(r, c)

    def test_no_one_without_instructor_filter(self):
        self.assertIsNone(self.get().context['one'])

    def test_menu_has_exactly_three_ai_links(self):
        html = self.get().content.decode()
        sub = html[html.index('id="aiMenu"'):]
        sub = sub[:sub.index('</div>', sub.index('class="submenu"'))]
        self.assertEqual(sub.count('class="nav-link'), 3 + 1)      # 3 links + the toggle button
        for gone in ('Student Feedback Analysis', 'Recommendations', 'Performance Forecasting'):
            self.assertNotIn(gone, sub)


class ReportExtrasTests(TrendsPageTests.__bases__[0]):
    def test_filters_and_limitations_in_report(self):
        from django.http import QueryDict
        from ai_insights.filters import parse_filters
        from ai_insights.services import reports
        rep = reports.analytics_report(parse_filters(QueryDict(f'instructor={self.teacher.pk}')), 'instructor')
        meta = dict(rep['meta'])
        self.assertIn('Selected filters', meta)
        self.assertIn('Instructor:', meta['Selected filters'])
        self.assertTrue(meta['Evaluations received (this period)'])
        last = rep['sections'][-1]
        self.assertEqual(last['heading'], 'Data Limitations')
        self.assertTrue(last['rows'])

    def test_excel_sheet_names_are_organised(self):
        import io, openpyxl
        from django.http import QueryDict
        from ai_insights.filters import parse_filters
        from ai_insights.services import reports
        rep = reports.analytics_report(parse_filters(QueryDict('')), 'school')
        names = openpyxl.load_workbook(io.BytesIO(reports.to_xlsx(rep))).sheetnames
        joined = ' '.join(names)
        for word in ('Instructor', 'Category', 'Historical', 'Forecast', 'Limitations'):
            self.assertIn(word, joined)