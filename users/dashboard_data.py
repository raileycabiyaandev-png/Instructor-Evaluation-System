from collections import OrderedDict

from django.db.models import Avg, Count
from django.utils import timezone

from academics.models import Department, TeachingAssignment
from ai_insights import conf as ai_conf
from evaluation.models import Evaluation, EvaluationPeriod

from .models import User

MIN_RESPONSES = ai_conf.MIN_RESPONSES
STEADY_BAND = ai_conf.STEADY_BAND

STATUS_LABELS = {
    'not_started': 'Not Started',
    'ongoing': 'Ongoing',
    'closed': 'Closed',
}

_AVGS = {
    'a1': Avg('teaching_effectiveness'),
    'a2': Avg('communication_skills'),
    'a3': Avg('classroom_management'),
    'a4': Avg('punctuality'),
    'a5': Avg('student_engagement'),
}


def _overall(row):
    """Average of the 5 category averages. None when there is nothing to average."""
    values = [row.get(k) for k in ('a1', 'a2', 'a3', 'a4', 'a5')]
    if any(v is None for v in values):
        return None
    return round(sum(values) / 5, 2)

def period_status(period, today):
    """'not_started', 'ongoing' or 'closed'."""
    if period.start_date > today:
        return 'not_started'
    if period.is_active and period.end_date >= today:
        return 'ongoing'
    return 'closed'


def period_words(period):
    return f"{period.get_term_display()} · {period.get_semester_display()} · S.Y. {period.school_year}"


def pick_period(today):
    from .views import period_sort_key       

    current = list(EvaluationPeriod.objects.filter(is_active=True, end_date__gte=today))
    if current:
        return max(current, key=period_sort_key), True

    with_results = list(EvaluationPeriod.objects.filter(evaluations__isnull=False).distinct())
    if with_results:
        return max(with_results, key=period_sort_key), False

    anything = list(EvaluationPeriod.objects.all())
    if anything:
        return max(anything, key=period_sort_key), False
    return None, False


def previous_period_with_results(period):
    from .views import period_sort_key

    mine = period_sort_key(period)
    earlier = [p for p in EvaluationPeriod.objects.filter(evaluations__isnull=False).distinct()
               if period_sort_key(p) < mine]
    return max(earlier, key=period_sort_key) if earlier else None

def evaluation_progress(period):
    
    pairs = set(
        TeachingAssignment.objects.filter(
            school_year=period.school_year, semester=period.semester,
            teacher__role='instructor', teacher__is_active=True,
        ).values_list('section_id', 'teacher_id').distinct()
    )
    sizes = dict(
        User.objects.filter(role='student', is_active=True, section__isnull=False)
        .values('section_id').annotate(n=Count('id')).values_list('section_id', 'n')
    )
    required = sum(sizes.get(section_id, 0) for section_id, _ in pairs)

    completed = 0
    done_rows = Evaluation.objects.filter(
        period=period, student__is_active=True, student__section__isnull=False, teacher__is_active=True,
    ).values_list('student__section_id', 'teacher_id')
    for section_id, teacher_id in done_rows:
        if (section_id, teacher_id) in pairs:
            completed += 1

    percent = round(completed / required * 100, 1) if required else None
    return {
        'required': required,
        'completed': completed,
        'remaining': max(required - completed, 0),
        'percent': percent,
        'percent_text': f"{percent:g}" if percent is not None else '0',
        'width': min(percent or 0, 100),
    }

def _person(teacher, row):
    return {
        'name': f"{teacher.first_name} {teacher.last_name}".strip(),
        'initials': f"{teacher.first_name[:1]}{teacher.last_name[:1]}".upper(),
        'overall': row['overall'],
        'count': row['n'],
    }


def departments_block(period):
    teachers = list(User.objects.filter(role='instructor', is_active=True).select_related('department'))
    by_dept = OrderedDict((d.pk, {'name': d.name, 'code': d.code, 'teachers': []})
                          for d in Department.objects.order_by('name'))
    no_dept = {'name': 'No department', 'code': '', 'teachers': []}
    for t in teachers:
        (by_dept[t.department_id] if t.department_id in by_dept else no_dept)['teachers'].append(t)

    stats = {}
    if period is not None:
        rows = (Evaluation.objects.filter(period=period, teacher__role='instructor', teacher__is_active=True)
                .values('teacher_id').annotate(n=Count('id'), **_AVGS))
        for row in rows:
            row['overall'] = _overall(row)
            stats[row['teacher_id']] = row

    blocks = list(by_dept.values())
    if no_dept['teachers']:
        blocks.append(no_dept)

    out = []
    for block in blocks:
        scored = [(t, stats[t.pk]) for t in block['teachers']
                  if t.pk in stats and stats[t.pk]['n'] >= MIN_RESPONSES and stats[t.pk]['overall'] is not None]
        best = poor = None
        if scored:
            best_t, best_row = sorted(scored, key=lambda x: (-x[1]['overall'], -x[1]['n'], x[0].last_name, x[0].first_name))[0]
            best = _person(best_t, best_row)
            if len(scored) > 1:
                poor_t, poor_row = sorted(scored, key=lambda x: (x[1]['overall'], -x[1]['n'], x[0].last_name, x[0].first_name))[0]
                poor = _person(poor_t, poor_row)
        out.append({
            'name': block['name'], 'code': block['code'], 'instructors': len(block['teachers']),
            'qualifying': len(scored), 'best': best, 'poor': poor,
            'only_one': len(scored) == 1,
        })
    return out

def _school_average(period):
    row = (Evaluation.objects.filter(period=period, teacher__role='instructor')
           .aggregate(n=Count('id'), **_AVGS))
    return _overall(row), row['n']


def school_block(period, status):
    if period is None:
        return {'average': None, 'count': 0, 'trend': None}
    average, count = _school_average(period)
    block = {'average': average, 'count': count, 'trend': None, 'previous': None, 'change': None,
             'partial': status == 'ongoing', 'width': round((average or 0) / 5 * 100, 1)}
    if average is None:
        return block

    previous = previous_period_with_results(period)
    if previous is None:
        block['trend'] = 'none'
        return block
    prev_average, _ = _school_average(previous)
    if prev_average is None:
        block['trend'] = 'none'
        return block

    change = round(average - prev_average, 2)
    block['previous'] = {'words': period_words(previous), 'average': prev_average}
    block['change'] = change
    if abs(change) < STEADY_BAND:
        block['trend'] = 'stable'
    else:
        block['trend'] = 'improving' if change > 0 else 'declining'
    return block

def build(today=None):
    today = today or timezone.now().date()
    period, is_current = pick_period(today)

    info = None
    status = None
    if period is not None:
        status = period_status(period, today)
        info = {
            'school_year': period.school_year,
            'semester': period.get_semester_display(),
            'term': period.get_term_display(),
            'words': period_words(period),
            'start': period.start_date,
            'end': period.end_date,
            'status': status,
            'status_label': STATUS_LABELS[status],
        }

    progress = evaluation_progress(period) if period is not None else None
    if info is not None:
        info['completion'] = progress['percent_text'] if progress and progress['required'] else None

    upcoming = None
    if not is_current:
        upcoming = (EvaluationPeriod.objects.filter(is_active=False, start_date__gt=today)
                    .order_by('start_date').first())

    return {
        'period': info,
        'is_current': is_current,
        'upcoming': {'words': period_words(upcoming), 'start': upcoming.start_date} if upcoming else None,
        'progress': progress,
        'school': school_block(period, status),
        'departments': departments_block(period),
        'min_responses': MIN_RESPONSES,
        'stats': {
            'students': User.objects.filter(role='student', is_active=True).count(),
            'instructors': User.objects.filter(role='instructor', is_active=True).count(),
            'departments': Department.objects.count(),
        },
    }