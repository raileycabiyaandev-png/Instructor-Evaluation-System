# Helpers shared by the AI tests. They make REAL rows in the temporary test database.

import datetime
from itertools import count

from django.contrib.auth.hashers import make_password
from django.utils import timezone

from evaluation.models import Evaluation, EvaluationPeriod
from users.models import User

TODAY = timezone.now().date()
_ids = count(1)

SEQUENCE = [
    ('2024-2025', '1st', 'prelims'), ('2024-2025', '1st', 'midterm'),
    ('2024-2025', '1st', 'prefinals'), ('2024-2025', '1st', 'finals'),
    ('2024-2025', '2nd', 'prelims'), ('2024-2025', '2nd', 'midterm'),
    ('2024-2025', '2nd', 'prefinals'), ('2024-2025', '2nd', 'finals'),
]


_PASSWORD_HASH = None


def _password_hash():
    """
    Hash the test password ONCE. Hashing is slow on purpose (it protects real passwords), and the
    tests make hundreds of fake students. Hashing each one made the tests take many minutes.
    """
    global _PASSWORD_HASH
    if _PASSWORD_HASH is None:
        _PASSWORD_HASH = make_password('Pass-12345!')
    return _PASSWORD_HASH


def make_user(email, role, **extra):
    user = User(email=email, username=email, role=role, first_name=email.split('@')[0].title(),
                last_name='Test', must_change_password=False, **extra)
    user.password = _password_hash()
    user.save()
    return user


def make_period(index, active=False, future=False):
    """Period number `index` of SEQUENCE. Finished (in the past) unless active/future."""
    year, sem, term = SEQUENCE[index]
    if future:
        start, end = TODAY + datetime.timedelta(days=5), TODAY + datetime.timedelta(days=15)
    elif active:
        start, end = TODAY - datetime.timedelta(days=2), TODAY + datetime.timedelta(days=10)
    else:
        start = TODAY - datetime.timedelta(days=400 - index * 40)
        end = start + datetime.timedelta(days=10)
    return EvaluationPeriod.objects.create(
        school_year=year, semester=sem, term=term, start_date=start, end_date=end, is_active=active)


def rate(period, teacher, scores, comments=None, categories=None):
    """
    One evaluation per score, each from a NEW student.
    scores: a number 1-5 for each student. categories: optional dict to change single categories.
    """
    was_active = period.is_active
    EvaluationPeriod.objects.filter(pk=period.pk).update(is_active=True)   # the model only accepts open periods
    period.refresh_from_db()
    made = []
    for i, score in enumerate(scores):
        n = next(_ids)
        student = make_user(f'student{n}@secret-school.com', 'student')
        student.first_name = f'Secretname{n}'
        student.save()
        values = dict(teaching_effectiveness=score, communication_skills=score, classroom_management=score,
                      punctuality=score, student_engagement=score)
        values.update(categories or {})
        made.append(Evaluation.objects.create(
            student=student, teacher=teacher, period=period,
            comment=(comments[i] if comments else ''), **values))
    EvaluationPeriod.objects.filter(pk=period.pk).update(is_active=was_active)
    period.refresh_from_db()
    return made


def spread(score, n):
    """n whole-number ratings (ratings are integers 1-5) whose average is `score`."""
    total = round(score * n)
    base, extra = divmod(total, n)
    return [base + 1] * extra + [base] * (n - extra)


def build_history(teacher, scores, responses=4):
    """Finished periods whose average score is each value in `scores` (oldest first)."""
    periods = []
    for i, score in enumerate(scores):
        p = make_period(i)
        rate(p, teacher, spread(score, responses))
        periods.append(p)
    return periods


def release(admin=None, period=None):
    """Make the administrator's official analysis 'finished', so instructors can see their AI results."""
    from django.utils import timezone

    from ai_insights.models import AnalysisRun
    from ai_insights.services.data import finished_periods
    period = period or [p for p in finished_periods() if p.evaluations.exists()][-1]
    version = AnalysisRun.objects.filter(period=period).count() + 1
    return AnalysisRun.objects.create(
        period=period, version=version, status='completed', started_by=admin,
        finished_at=timezone.now(), message='test run')