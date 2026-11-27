# evaluation/seed.py
#
# PUTS QUESTIONNAIRE VERSION 1 INTO THE DATABASE (safe to run many times)
#
# Migration 0009 already does this once, when you run `migrate`. This file does the same thing with the
# normal models, so it can also be used by the tests: one kind of test (TransactionTestCase) EMPTIES the
# database when it ends, and a test database that is kept (--keepdb) would otherwise have no questionnaire
# on the next run.
#
# It never changes a questionnaire that already exists.

from django.db import transaction

from .models import EvaluationCategory, EvaluationQuestion, Questionnaire
from .questionnaire_data import DIMENSIONS


@transaction.atomic
def ensure_questionnaire_v1():
    questionnaire, created = Questionnaire.objects.get_or_create(
        version=1, defaults={'name': 'Instructor Evaluation', 'is_active': True})
    if not created:
        return questionnaire
    for order, (key, name, description, statements) in enumerate(DIMENSIONS, start=1):
        category = EvaluationCategory.objects.create(
            questionnaire=questionnaire, key=key, name=name, description=description, order=order)
        for number, text in enumerate(statements, start=1):
            EvaluationQuestion.objects.create(category=category, text=text, order=number)
    return questionnaire
