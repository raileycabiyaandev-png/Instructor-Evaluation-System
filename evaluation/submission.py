# evaluation/submission.py
#
# SAVING ONE STUDENT'S EVALUATION (the 15 ratings + the optional comment)
#
# The view does the web part (login, "may this student evaluate this teacher?"). This file does the
# DATA part, so the rules live in one place and can be tested without a web page:
#
#   1. The ratings must be for EXACTLY the statements of the period's questionnaire:
#      no statement missing, none extra, none from another questionnaire.
#   2. Every rating is a whole number from 1 to 5.
#   3. The Evaluation row and its 15 answers are saved in ONE database transaction:
#      either everything is saved or nothing is (never a half-finished evaluation).
#   4. One evaluation per student + instructor + period. The database enforces it too, so two
#      clicks at the same moment cannot create two copies.
#
# Also here: build_form() (what the student page draws) and create_evaluation() (a short helper
# for tests and demo-data scripts).

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Evaluation, EvaluationAnswer, EvaluationPeriod
from .questionnaire_data import LIKERT

COMMENT_MAX = 1000


class SubmissionError(Exception):
    """The evaluation was not saved. str(error) is a message that is safe to show to the student."""


class AlreadySubmitted(SubmissionError):
    """This student already evaluated this instructor in this period."""


def field_name(question):
    """Name of the form field of one statement (the page and the view must agree on it)."""
    return f"q_{question.pk}"


def build_form(period):
    """
    What the student page draws: the period's dimensions, each with its statements.
    [{'key', 'name', 'description', 'questions': [{'id', 'field', 'text', 'number'}]}]
    Returns [] when the period has no questionnaire.
    """
    if not period.questionnaire_id:
        return []
    groups, by_key, number = [], {}, 0
    for question in period.questionnaire.questions():
        category = question.category
        if category.pk not in by_key:
            by_key[category.pk] = {'key': category.key, 'name': category.name,
                                   'description': category.description, 'questions': []}
            groups.append(by_key[category.pk])
        number += 1
        by_key[category.pk]['questions'].append(
            {'id': question.pk, 'field': field_name(question), 'text': question.text, 'number': number})
    return groups


def parse_rating(value):
    """'4' -> 4. Anything that is not a whole number from 1 to 5 gives None."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 5 and str(value).strip() == str(number) else None


def read_ratings(data, period):
    """
    Reads {question id: rating} from the posted form. Raises SubmissionError when any statement
    is missing or any rating is not 1-5.
    """
    questions = list(period.questionnaire.questions()) if period.questionnaire_id else []
    if not questions:
        raise SubmissionError("This evaluation has no questions yet. Please tell your administrator.")
    ratings = {}
    for question in questions:
        rating = parse_rating(data.get(field_name(question)))
        if rating is None:
            raise SubmissionError("Please rate every statement from 1 to 5.")
        ratings[question.pk] = rating
    return ratings


def save_evaluation(student, teacher, period, ratings, comment=''):
    """
    Saves the evaluation and its answers together. `ratings` is {question id: rating}.
    Raises AlreadySubmitted or SubmissionError (never a half-saved evaluation).
    """
    expected = {q.pk for q in period.questionnaire.questions()} if period.questionnaire_id else set()
    if not expected:
        raise SubmissionError("This evaluation has no questions yet. Please tell your administrator.")
    if set(ratings) != expected:
        raise SubmissionError("Please rate every statement from 1 to 5.")
    if any(parse_rating(r) is None for r in ratings.values()):
        raise SubmissionError("Ratings must be whole numbers from 1 to 5.")

    try:
        with transaction.atomic():
            if Evaluation.objects.filter(student=student, teacher=teacher, period=period).exists():
                raise AlreadySubmitted("You already evaluated this instructor in this period.")
            evaluation = Evaluation(student=student, teacher=teacher, period=period,
                                    comment=(comment or '').strip()[:COMMENT_MAX])
            evaluation.save()                          # runs Evaluation.clean (roles, period open, questionnaire)
            EvaluationAnswer.objects.bulk_create([
                EvaluationAnswer(evaluation=evaluation, question_id=qid, rating=int(rating))
                for qid, rating in ratings.items()
            ])
    except IntegrityError:
        raise AlreadySubmitted("You already evaluated this instructor in this period.")
    except ValidationError as exc:
        raise SubmissionError(' '.join(exc.messages) or "Your evaluation could not be saved.")
    return evaluation


# ─────────────────────────────────────────────
# Helper for tests and demo-data scripts (NOT used by the website)
# ─────────────────────────────────────────────
def create_evaluation(student, teacher, period, scores=4, comment='', check_period_open=True):
    """
    Makes one real evaluation with its 15 answers.
      scores = 4                         every statement 4
      scores = {'punctuality...': 5}     per dimension: one number for all 3, or a list of 3
                                         (dimensions not listed get 4)
    check_period_open=False lets a test fill a CLOSED period (the website never allows that).
    """
    questions = list(period.questionnaire.questions())
    per_category = {}
    for question in questions:
        per_category.setdefault(question.category.key, []).append(question)

    ratings = {}
    for key, qs in per_category.items():
        value = scores.get(key, 4) if isinstance(scores, dict) else scores
        values = list(value) if isinstance(value, (list, tuple)) else [value] * len(qs)
        for question, rating in zip(qs, values):
            ratings[question.pk] = rating

    if check_period_open:
        return save_evaluation(student, teacher, period, ratings, comment)
    with transaction.atomic():
        evaluation = Evaluation(student=student, teacher=teacher, period=period, comment=comment)
        super(Evaluation, evaluation).save()           # skips the "period is open" check on purpose
        EvaluationAnswer.objects.bulk_create([
            EvaluationAnswer(evaluation=evaluation, question_id=qid, rating=r) for qid, r in ratings.items()])
    return evaluation


def bulk_create_evaluations(evaluations):
    """
    FAST version of create_evaluation for demo-data scripts (many rows at once, NOT used by the website).
    Each item is an UNSAVED Evaluation with an extra attribute `.scores` (same meaning as in create_evaluation:
    a number, or {dimension key: number or list of 3}). Periods may be closed. Returns how many were saved.
    """
    layouts = {}                                           # questionnaire id -> {dimension key: [questions]}
    for ev in evaluations:
        qid = ev.period.questionnaire_id
        if qid not in layouts:
            per = {}
            for question in ev.period.questionnaire.questions():
                per.setdefault(question.category.key, []).append(question)
            layouts[qid] = per

    with transaction.atomic():
        Evaluation.objects.bulk_create(evaluations)
        answers = []
        for ev in evaluations:
            for key, qs in layouts[ev.period.questionnaire_id].items():
                value = ev.scores.get(key, 4) if isinstance(ev.scores, dict) else ev.scores
                values = list(value) if isinstance(value, (list, tuple)) else [value] * len(qs)
                answers += [EvaluationAnswer(evaluation=ev, question=q, rating=r) for q, r in zip(qs, values)]
        EvaluationAnswer.objects.bulk_create(answers, batch_size=2000)
    return len(evaluations)
