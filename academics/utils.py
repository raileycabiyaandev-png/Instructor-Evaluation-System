# academics/utils.py
from collections import OrderedDict

from django.utils import timezone

from evaluation.models import Evaluation, EvaluationPeriod
from .models import TeachingAssignment


def semester_options():
    """
    All (school_year, semester) pairs that exist in Evaluation Periods,
    newest first. Example: [('2026-2027', '1st'), ('2025-2026', '2nd')]

    Teaching assignments are made per SEMESTER, but an Evaluation Period
    is per TERM (prelims, midterm, ...). So we only need the pair.
    """
    pairs = []
    seen = set()

    for period in EvaluationPeriod.objects.order_by('-school_year', '-semester', '-start_date'):
        key = (period.school_year, period.semester)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    return pairs


def current_semester():
    """
    The semester of the ACTIVE Evaluation Period.
    If none is active, use the most recent Evaluation Period.
    Returns (school_year, semester) or None when no period exists yet.
    """
    period = (
        EvaluationPeriod.objects.filter(is_active=True).first()
        or EvaluationPeriod.objects.order_by('-start_date').first()
    )

    if period is None:
        return None

    return (period.school_year, period.semester)


def semester_label(school_year, semester):
    names = dict(EvaluationPeriod.SEMESTER_CHOICES)
    return f"S.Y. {school_year} - {names.get(semester, semester)}"


def encode_semester(school_year, semester):
    return f"{school_year}|{semester}"


def decode_semester(value):
    school_year, _, semester = value.partition('|')
    return school_year, semester


# ─────────────────────────────────────────────────────────────
# STUDENT SIDE: which instructors may a student evaluate?
#
# Rule: a student may only evaluate the instructors who teach THEIR
# section in the semester of the open Evaluation Period.
# ─────────────────────────────────────────────────────────────

def get_open_period():
    """
    The Evaluation Period students can use right now, or None.

    Periods whose end date has passed are closed first, so a student can
    never submit after the deadline even if nobody pressed "Close".
    """
    today = timezone.now().date()
    EvaluationPeriod.objects.filter(is_active=True, end_date__lt=today).update(is_active=False)
    return EvaluationPeriod.objects.filter(is_active=True).first()


def assignments_for_student(student, period):
    """Teaching assignments of the student's section for the period's semester."""
    if period is None or not student.section_id:
        return TeachingAssignment.objects.none()

    return TeachingAssignment.objects.filter(
        section_id=student.section_id,
        school_year=period.school_year,
        semester=period.semester,
        teacher__role='instructor',
        teacher__is_active=True,
    ).select_related('teacher', 'teacher__department', 'subject')


def instructor_cards_for_student(student, period):
    """
    One card per instructor (an instructor who teaches 2 subjects to the
    section still appears once, because a student evaluates an instructor
    once per period).

    [{'teacher': User, 'subjects': [Subject, ...], 'is_evaluated': bool}, ...]
    """
    assignments = assignments_for_student(student, period).order_by(
        'teacher__last_name', 'teacher__first_name', 'subject__code'
    )

    evaluated_ids = set(
        Evaluation.objects.filter(student=student, period=period)
        .values_list('teacher_id', flat=True)
    ) if period else set()

    cards = OrderedDict()
    for assignment in assignments:
        card = cards.setdefault(assignment.teacher_id, {
            'teacher': assignment.teacher,
            'subjects': [],
            'is_evaluated': assignment.teacher_id in evaluated_ids,
        })
        card['subjects'].append(assignment.subject)

    return list(cards.values())


def subjects_taught_to_student(student, teacher, period):
    """Subjects this teacher teaches to the student's section (empty list = not allowed)."""
    return [
        a.subject
        for a in assignments_for_student(student, period).filter(teacher=teacher).order_by('subject__code')
    ]