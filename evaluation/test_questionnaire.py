# evaluation/test_questionnaire.py
#
# TESTS FOR THE 15-STATEMENT QUESTIONNAIRE
#   1. The questionnaire itself: 5 dimensions x 3 statements, the wording you approved.
#   2. Saving: only valid evaluations are saved, duplicates / bad ratings / closed periods are refused.
#   3. Scoring: category and overall averages checked against numbers worked out BY HAND.
#   4. Versions: a period keeps its questionnaire and cannot switch once students answered.
#   5. Pages: the student form shows 15 statements; results pages never show the student.

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from evaluation import scoring, submission
from evaluation.models import (Evaluation, EvaluationAnswer, EvaluationCategory, EvaluationPeriod,
                               EvaluationQuestion, Questionnaire)
from evaluation.questionnaire_data import CATEGORY_KEYS, DIMENSIONS, TOTAL_STATEMENTS
from evaluation.submission import AlreadySubmitted, SubmissionError, create_evaluation

from .test_student_flow import StudentBase, make_user, ratings_data


class QuestionnaireContent(TestCase):
    def test_five_dimensions_with_three_statements_each(self):
        q = Questionnaire.current()
        self.assertEqual(q.version, 1)
        cats = list(q.categories.order_by('order'))
        self.assertEqual([c.key for c in cats], CATEGORY_KEYS)
        for cat in cats:
            self.assertEqual(cat.questions.count(), 3, cat.key)
        self.assertEqual(len(list(q.questions())), TOTAL_STATEMENTS)

    def test_the_approved_wording_is_in_the_database(self):
        stored = [qq.text for qq in Questionnaire.current().questions()]
        wanted = [text for _, _, _, statements in DIMENSIONS for text in statements]
        self.assertEqual(stored, wanted)
        self.assertEqual(stored[0], 'Shows strong knowledge of the subject.')
        self.assertEqual(stored[-1], 'Consistently fulfills teaching responsibilities, such as following the '
                                     'schedule and returning work on time.')

    def test_new_period_gets_the_current_questionnaire(self):
        today = timezone.now().date()
        p = EvaluationPeriod.objects.create(school_year='2030-2031', semester='1st', term='prelims',
                                            start_date=today, end_date=today + datetime.timedelta(days=3))
        self.assertEqual(p.questionnaire, Questionnaire.current())


class SavingRules(StudentBase):
    def ratings(self, value=4):
        return {q.pk: value for q in self.period.questionnaire.questions()}

    def save(self, ratings=None, student=None, teacher=None, comment=''):
        return submission.save_evaluation(student or self.student, teacher or self.prof_ge, self.period,
                                          self.ratings() if ratings is None else ratings, comment)

    def test_valid_evaluation_saves_15_answers(self):
        ev = self.save(comment='  Nice  ')
        self.assertEqual(ev.answers.count(), 15)
        self.assertEqual(ev.comment, 'Nice')

    def test_second_submission_is_refused_and_keeps_only_one(self):
        self.save()
        with self.assertRaises(AlreadySubmitted):
            self.save()
        self.assertEqual(Evaluation.objects.count(), 1)
        self.assertEqual(EvaluationAnswer.objects.count(), 15)

    def test_database_blocks_a_duplicate_even_without_the_service(self):
        self.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Evaluation.objects.bulk_create([Evaluation(student=self.student, teacher=self.prof_ge, period=self.period)])

    def test_bad_ratings_are_refused_and_nothing_is_saved(self):
        for bad in (0, 6, -1, 'x', None, 4.5, True):
            ratings = self.ratings()
            ratings[next(iter(ratings))] = bad
            with self.assertRaises(SubmissionError, msg=repr(bad)):
                self.save(ratings)
        self.assertEqual(Evaluation.objects.count(), 0)
        self.assertEqual(EvaluationAnswer.objects.count(), 0)

    def test_missing_or_extra_statement_is_refused(self):
        missing = self.ratings(); missing.popitem()
        with self.assertRaises(SubmissionError):
            self.save(missing)
        extra = self.ratings(); extra[999999] = 3
        with self.assertRaises(SubmissionError):
            self.save(extra)
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_statement_of_another_questionnaire_is_refused(self):
        other = Questionnaire.objects.create(version=2, name='v2')
        cat = EvaluationCategory.objects.create(questionnaire=other, key='teaching_competence', name='T', order=1)
        foreign = EvaluationQuestion.objects.create(category=cat, text='Other?', order=1)
        ratings = self.ratings(); ratings.popitem(); ratings[foreign.pk] = 4
        with self.assertRaises(SubmissionError):
            self.save(ratings)

    def test_closed_period_is_refused(self):
        EvaluationPeriod.objects.filter(pk=self.period.pk).update(is_active=False)
        self.period.refresh_from_db()
        with self.assertRaises(SubmissionError):
            self.save()
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_only_students_can_evaluate_and_only_instructors_can_be_evaluated(self):
        with self.assertRaises(SubmissionError):
            self.save(student=self.prof_it)
        with self.assertRaises(SubmissionError):
            self.save(teacher=self.classmate)
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_database_refuses_a_rating_outside_1_to_5(self):
        ev = self.save()
        answer = ev.answers.first()
        for bad in (0, 6):
            with self.assertRaises(IntegrityError), transaction.atomic():
                EvaluationAnswer.objects.filter(pk=answer.pk).update(rating=bad)

    def test_one_answer_per_statement_per_evaluation(self):
        ev = self.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EvaluationAnswer.objects.create(evaluation=ev, question=ev.answers.first().question, rating=3)

    def test_half_saved_evaluation_is_rolled_back(self):
        ratings = self.ratings()
        original = EvaluationAnswer.objects.bulk_create
        def boom(*a, **k):
            raise IntegrityError('simulated crash')
        EvaluationAnswer.objects.bulk_create = boom
        try:
            with self.assertRaises(AlreadySubmitted):        # the service reports any IntegrityError this way
                self.save(ratings)
        finally:
            EvaluationAnswer.objects.bulk_create = original
        self.assertEqual(Evaluation.objects.count(), 0)


class SubmitView(StudentBase):
    def test_full_form_saves_and_comment_stays_optional(self):
        r = self.client.post(self.submit_url(self.prof_ge), ratings_data(comment=''))
        self.assertRedirects(r, reverse('student_dashboard'), fetch_redirect_response=False)
        ev = Evaluation.objects.get()
        self.assertEqual((ev.comment, ev.answers.count()), ('', 15))

    def test_missing_statement_in_the_form_is_refused(self):
        data = ratings_data()
        data.pop(next(k for k in data if k.startswith('q_')))
        self.client.post(self.submit_url(self.prof_ge), data)
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_student_of_another_section_cannot_evaluate(self):
        self.client.logout()
        other = make_user('other@test.com', 'student', program='it', section=self.sec_b)
        self.client.force_login(other)
        self.client.post(self.submit_url(self.prof_it), ratings_data())
        self.assertEqual(Evaluation.objects.count(), 0)

    def test_form_page_shows_15_statements_in_5_groups(self):
        r = self.client.get(self.evaluate_url(self.prof_ge))
        self.assertEqual(len(r.context['groups']), 5)
        self.assertEqual(sum(len(g['questions']) for g in r.context['groups']), 15)
        html = r.content.decode()
        self.assertEqual(html.count('type="radio"'), 15 * 5)
        for text in ('Shows strong knowledge of the subject.', 'Excellent', 'Poor'):
            self.assertIn(text, html)


class ScoringByHand(StudentBase):
    """Numbers below were worked out on paper first."""

    def setUp(self):
        super().setUp()
        self.mates = [make_user(f'm{i}@test.com', 'student', program='it', section=self.sec_a) for i in range(2)]

    def test_category_and_overall_average(self):
        # student 1: teaching 5,4,3 (avg 4)  communication 5,5,5 (avg 5)  others all 2
        create_evaluation(self.mates[0], self.prof_it, self.period, scores={
            'teaching_competence': [5, 4, 3], 'communication_clarity': 5,
            'classroom_management': 2, 'student_engagement_support': 2, 'professionalism_reliability': 2})
        # student 2: everything 4
        create_evaluation(self.mates[1], self.prof_it, self.period, scores=4)
        s = scoring.period_summary(self.prof_it, self.period)
        self.assertEqual(s['responses'], 2)
        c = s['categories']
        self.assertAlmostEqual(c['teaching_competence'], (4 + 4) / 2)        # 4.0
        self.assertAlmostEqual(c['communication_clarity'], (5 + 4) / 2)      # 4.5
        for key in ('classroom_management', 'student_engagement_support', 'professionalism_reliability'):
            self.assertAlmostEqual(c[key], (2 + 4) / 2)                      # 3.0
        self.assertAlmostEqual(s['overall'], (4 + 4.5 + 3 + 3 + 3) / 5)      # 3.5

    def test_weights_change_the_overall_but_not_the_categories(self):
        create_evaluation(self.mates[0], self.prof_it, self.period, scores={'teaching_competence': 5})   # rest are 4
        EvaluationCategory.objects.filter(questionnaire=self.period.questionnaire,
                                          key='teaching_competence').update(weight=Decimal('3'))
        s = scoring.period_summary(self.prof_it, self.period)
        self.assertAlmostEqual(s['categories']['teaching_competence'], 5)
        self.assertAlmostEqual(s['overall'], (3 * 5 + 4 * 4) / 7)            # 31 / 7 = 4.428...

    def test_no_evaluations_means_no_scores_never_invented(self):
        self.assertIsNone(scoring.period_summary(self.prof_it, self.period))
        s = scoring.summary(Evaluation.objects.none())
        self.assertEqual((s['responses'], s['overall']), (0, None))
        self.assertTrue(all(v is None for v in s['categories'].values()))

    def test_one_instructor_never_mixes_with_another(self):
        create_evaluation(self.mates[0], self.prof_it, self.period, scores=5)
        create_evaluation(self.mates[0], self.prof_ge, self.period, scores=2)
        got = scoring.grouped(Evaluation.objects.all(), 'teacher_id')
        self.assertAlmostEqual(got[(self.prof_it.pk,)]['overall'], 5)
        self.assertAlmostEqual(got[(self.prof_ge.pk,)]['overall'], 2)

    def test_grouped_uses_a_fixed_number_of_queries(self):
        for i, teacher in enumerate((self.prof_it, self.prof_ge, self.prof_b)):
            create_evaluation(self.mates[0], teacher, self.period, scores=3 + i % 2)
        with self.assertNumQueries(4):     # counts, answers, weights, and the questionnaire lookup
            scoring.grouped(Evaluation.objects.all(), 'teacher_id')

    def test_single_evaluation_overall(self):
        ev = create_evaluation(self.mates[0], self.prof_it, self.period, scores={'classroom_management': [1, 2, 3]})
        self.assertEqual(scoring.evaluation_overall(ev), round((4 + 4 + 2 + 4 + 4) / 5, 2))
        self.assertEqual(ev.average_score(), scoring.evaluation_overall(ev))


class Versions(StudentBase):
    def test_period_questionnaire_is_locked_after_answers(self):
        create_evaluation(self.classmate, self.prof_it, self.period, scores=4)
        v2 = Questionnaire.objects.create(version=2, name='v2')
        self.period.questionnaire = v2
        with self.assertRaises(ValidationError):
            self.period.full_clean()

    def test_old_period_keeps_old_version_when_a_new_one_appears(self):
        v1 = self.period.questionnaire
        Questionnaire.objects.create(version=2, name='v2', is_active=True)
        self.period.save()
        self.period.refresh_from_db()
        self.assertEqual(self.period.questionnaire, v1)

    def test_questionnaire_in_use_cannot_be_deleted(self):
        with self.assertRaises(Exception):
            Questionnaire.objects.get(pk=self.period.questionnaire_id).delete()


class PagesStillWork(StudentBase):
    def test_student_history_shows_overall_without_extra_queries(self):
        create_evaluation(self.student, self.prof_ge, self.period, scores=4)
        r = self.client.get(reverse('student_dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['history'][0].overall, 4.0)

    def test_admin_pages_never_show_the_student(self):
        create_evaluation(self.student, self.prof_ge, self.period, scores=4, comment='ok')
        admin = make_user('boss@test.com', 'admin')
        self.client.force_login(admin)
        for name in ('admin_dashboard',):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, 'stud@test.com')
