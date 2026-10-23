# academics/utils.py
from evaluation.models import EvaluationPeriod


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
