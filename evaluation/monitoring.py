# evaluation/monitoring.py
#
# EVALUATION MONITORING (Admin): how many evaluations are required, done and left.
# All the maths is here, so the view stays short and the rules can be tested without a web page.
#
# THE RULES (the same words are shown on the page)
#   * One evaluation period = one school year + semester + term.
#   * REQUIRED  = every active student evaluates every active instructor who teaches the
#                 student's SECTION in the school year + semester of the period.
#                 (Same rule the student pages use, see academics/utils.py.)
#   * An instructor who teaches 2 subjects to the same section is counted ONCE, because a student
#     evaluates an instructor once per period (Evaluation is unique per student + teacher + period).
#     Two different instructors in one section are counted as two evaluations.
#   * COMPLETED = an Evaluation row of THAT period for a required pair (student, instructor).
#   * COMPLETION % = completed / required x 100.
#   * Student status: Completed (all done), Partially completed (some done), Not started (none done).
#
# PRIVACY: this file reads only WHO has evaluated WHOM (ids). It never reads ratings or comments,
# so a monitoring page cannot leak them. The Excel / PDF export has no student names at all.
#
# SPEED: 4 small queries in total (assignments, students, evaluations, teachers). The counting is
# done in memory with sets, so there is no query inside a loop (no N+1).

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Optional

from django.utils import timezone

from academics.models import Department, Section, TeachingAssignment
from users.dashboard_data import period_status, period_words
from users.models import User

from .models import Evaluation, EvaluationPeriod

STATUS_LABELS = {'not_started': 'Not Started', 'ongoing': 'Ongoing', 'closed': 'Closed'}

# Student status keys (also used in the ?status= filter of the student table)
COMPLETED, PARTIAL, NOT_STARTED = 'completed', 'partial', 'not_started'
STUDENT_STATUS_LABELS = OrderedDict([
    (COMPLETED, 'Completed'),
    (PARTIAL, 'Partially completed'),
    (NOT_STARTED, 'Not started'),
])


SECTION_SORTS = OrderedDict([('low', 'Lowest first'), ('high', 'Highest first'), ('name', 'Name A-Z')])


# ─────────────────────────────────────────────
# Filters (every value from the URL is checked here)
# ─────────────────────────────────────────────
@dataclass
class Filters:
    school_year: str = ''
    semester: str = ''
    term: str = ''
    department: Optional[Department] = None
    section: Optional[Section] = None
    instructor: Optional[User] = None
    status: str = ''                     # student table only: completed / partial / not_started / ''
    student_section: Optional[Section] = None   # student table only: "View students" of one section
    q: str = ''                          # student table only: part of a student's name
    section_sort: str = ''               # sections table only: low / high / name

    @property
    def any_scope(self):
        """True when the admin narrowed the numbers to a department, section or instructor."""
        return bool(self.department or self.section or self.instructor)


def _as_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_filters(params):
    """Build Filters from request.GET. Anything unknown or invalid is ignored (never an error)."""
    school_year = (params.get('school_year') or '').strip()[:20]
    semester = params.get('semester') if params.get('semester') in dict(EvaluationPeriod.SEMESTER_CHOICES) else ''
    term = params.get('term') if params.get('term') in dict(EvaluationPeriod.TERM_CHOICES) else ''
    status = params.get('status') if params.get('status') in STUDENT_STATUS_LABELS else ''

    department = section = instructor = None
    if _as_int(params.get('department')):
        department = Department.objects.filter(pk=_as_int(params.get('department'))).first()
    if _as_int(params.get('section')):
        section = Section.objects.filter(pk=_as_int(params.get('section'))).first()
    if _as_int(params.get('instructor')):
                instructor = (User.objects.filter(pk=_as_int(params.get('instructor')), role='instructor', is_active=True)
                      .select_related('department').first())
    student_section = None
    if _as_int(params.get('ssection')):
        student_section = Section.objects.filter(pk=_as_int(params.get('ssection'))).first()
    q = ' '.join((params.get('q') or '').split())[:50]
    section_sort = params.get('ssort') if params.get('ssort') in SECTION_SORTS else ''
    return Filters(school_year, semester, term, department, section, instructor, status,
                   student_section, q, section_sort)


# ─────────────────────────────────────────────
# Which period?
# ─────────────────────────────────────────────
def _newest(periods):
    from users.views import period_sort_key          # imported here: users.views imports a lot of modules

    return max(periods, key=lambda p: (period_sort_key(p), p.start_date, p.pk)) if periods else None


def choose_period(filters, today):
    """
    Returns (period or None, is_default).
      * Nothing chosen  -> the open period; otherwise the latest period (same rule as the dashboard).
      * Year / semester / term chosen -> the newest period that matches what was chosen.
    A combination that does not exist gives (None, False) and the page says so.
    """
    if not (filters.school_year or filters.semester or filters.term):
        from users.dashboard_data import pick_period
        period, _ = pick_period(today)
        return period, True

    rows = EvaluationPeriod.objects.all()
    if filters.school_year:
        rows = rows.filter(school_year=filters.school_year)
    if filters.semester:
        rows = rows.filter(semester=filters.semester)
    if filters.term:
        rows = rows.filter(term=filters.term)
    return _newest(list(rows)), False


def period_options():
    """Values for the three period drop-downs (only values that really exist)."""
    years = list(EvaluationPeriod.objects.order_by('-school_year').values_list('school_year', flat=True).distinct())
    return {
        'school_years': years,
        'semesters': EvaluationPeriod.SEMESTER_CHOICES,
        'terms': EvaluationPeriod.TERM_CHOICES,
    }


def period_block(period, today):
    """Period facts for the top card: status, dates and the days left."""
    status = period_status(period, today)
    if status == 'not_started':
        days = (period.start_date - today).days
        timing = f"Starts in {days} day{'s' if days != 1 else ''}"
    elif status == 'ongoing':
        days = (period.end_date - today).days
        timing = 'Ends today' if days == 0 else f"{days} day{'s' if days != 1 else ''} remaining"
    elif period.end_date >= today:
        days = None
        timing = 'Switched off by the administrator'
    else:
        days = (today - period.end_date).days
        timing = 'Closed today' if days == 0 else f"Closed {days} day{'s' if days != 1 else ''} ago"
    total = (period.end_date - period.start_date).days + 1
    return {
        'pk': period.pk,
        'words': period_words(period),
        'school_year': period.school_year,
        'semester': period.get_semester_display(),
        'term': period.get_term_display(),
        'semester_key': period.semester,
        'term_key': period.term,
        'start': period.start_date,
        'end': period.end_date,
        'status': status,
        'status_label': STATUS_LABELS[status],
        'timing': timing,
        'days_left': days if status == 'ongoing' else None,
        'length_days': max(total, 0),
        'dates_look_wrong': period.end_date < period.start_date,
    }


# ─────────────────────────────────────────────
# The numbers
# ─────────────────────────────────────────────
def _percent(done, required):
    """None when nothing is required (so the page shows a dash, never a made-up 0% or 100%)."""
    if not required:
        return None
    return round(done / required * 100, 1)


def _row_numbers(required, done):
    percent = _percent(done, required)
    return {
        'required': required,
        'completed': done,
        'remaining': max(required - done, 0),
        'percent': percent,
        'percent_text': f"{percent:g}" if percent is not None else '—',
        'width': min(percent or 0, 100),
    }


def _student_status(required, done):
    if required and done >= required:
        return COMPLETED
    if done > 0:
        return PARTIAL
    return NOT_STARTED


def _required_pairs(period, filters):
    """
    The set of (section_id, teacher_id) pairs that must be evaluated in this period.
    A set removes duplicates: one instructor teaching 2 subjects to a section is ONE pair.
    """
    rows = TeachingAssignment.objects.filter(
        school_year=period.school_year, semester=period.semester,
        teacher__role='instructor', teacher__is_active=True,
    )
    if filters.department:
        rows = rows.filter(teacher__department=filters.department)
    if filters.section:
        rows = rows.filter(section=filters.section)
    if filters.instructor:
        rows = rows.filter(teacher=filters.instructor)
    # order_by() clears the model's default ordering, which would otherwise be added to the query
    # and stop distinct() from removing the duplicates. The set() is the real guarantee.
    return set(rows.order_by().values_list('section_id', 'teacher_id').distinct())


def calculate(period, filters):
    """
    Returns the full picture for ONE period (see the keys at the bottom).
    Nothing here is stored: it is counted from the real records every time the page opens.
    """
    pairs = _required_pairs(period, filters)
    teachers_by_section = defaultdict(set)
    for section_id, teacher_id in pairs:
        teachers_by_section[section_id].add(teacher_id)

    # 1 query: the active students of those sections
    students = list(
        User.objects.filter(role='student', is_active=True, section_id__in=list(teachers_by_section))
        .order_by('last_name', 'first_name', 'pk')
        .values_list('pk', 'first_name', 'last_name', 'section_id', 'section__name', 'section__department__name')
    ) if teachers_by_section else []

    # 1 query: who evaluated whom in this period (ids only: no ratings, no comments)
    done_pairs = set(
        Evaluation.objects.filter(
            period=period,
            student__section_id__in=list(teachers_by_section),
            teacher_id__in={t for ts in teachers_by_section.values() for t in ts},
        ).values_list('student_id', 'teacher_id')
    ) if students else set()

    # 1 query: the instructors that appear (name + department)
    teacher_ids = {t for ts in teachers_by_section.values() for t in ts}
    teachers = {
        t.pk: t for t in User.objects.filter(pk__in=teacher_ids).select_related('department')
    } if teacher_ids else {}

    per_teacher = defaultdict(lambda: {'required': 0, 'done': 0, 'sections': set()})
    per_section = {}
    student_rows = []
    counts = {COMPLETED: 0, PARTIAL: 0, NOT_STARTED: 0}
    total_required = total_done = 0

    for pk, first, last, section_id, section_name, section_dept in students:
        needed = teachers_by_section[section_id]
        finished = [t for t in needed if (pk, t) in done_pairs]
        total_required += len(needed)
        total_done += len(finished)
        status = _student_status(len(needed), len(finished))
        counts[status] += 1
        sec = per_section.setdefault(section_id, {
            'pk': section_id, 'name': section_name or '', 'department': section_dept or 'No department',
            'required': 0, 'done': 0, 'students': 0,
            'counts': {COMPLETED: 0, PARTIAL: 0, NOT_STARTED: 0}})
        sec['required'] += len(needed)
        sec['done'] += len(finished)
        sec['students'] += 1
        sec['counts'][status] += 1
        for t in needed:
            bucket = per_teacher[t]
            bucket['required'] += 1
            bucket['sections'].add(section_id)
            if (pk, t) in done_pairs:
                bucket['done'] += 1
        student_rows.append({
            'name': f"{last}, {first}".strip(', ') or 'Unnamed student',
            'section': section_name or '',
            'section_id': section_id,
            'status': status,
            'status_label': STUDENT_STATUS_LABELS[status],
            **_row_numbers(len(needed), len(finished)),
        })

    # Instructors: lowest completion first, so the ones who need follow-up are on top
    instructors = []
    dept_totals = {}
    for teacher_id, bucket in per_teacher.items():
        teacher = teachers.get(teacher_id)
        if teacher is None:
            continue
        dept = teacher.department
        dept_key = dept.pk if dept else 0
        dept_name = dept.name if dept else 'No department'
        dept_row = dept_totals.setdefault(dept_key, {
            'name': dept_name, 'code': dept.code if dept else '',
            'required': 0, 'done': 0, 'instructors': 0,
        })
        dept_row['required'] += bucket['required']
        dept_row['done'] += bucket['done']
        dept_row['instructors'] += 1
        numbers = _row_numbers(bucket['required'], bucket['done'])
        instructors.append({
            'pk': teacher.pk,
            'name': f"{teacher.last_name}, {teacher.first_name}".strip(', ') or teacher.email,
            'department': dept_name,
            'sections': len(bucket['sections']),
            'status': _student_status(bucket['required'], bucket['done']),
            **numbers,
        })
    instructors.sort(key=lambda r: (r['percent'] if r['percent'] is not None else -1, r['name'].lower()))

    overall = _row_numbers(total_required, total_done)

    sections = []
    for sec in per_section.values():
        sections.append({
            'pk': sec['pk'], 'name': sec['name'], 'department': sec['department'], 'students': sec['students'],
            'completed_students': sec['counts'][COMPLETED], 'partial_students': sec['counts'][PARTIAL],
            'not_started_students': sec['counts'][NOT_STARTED],
            **_row_numbers(sec['required'], sec['done']),
        })

    # Departments: highest completion first, with the gap against the school average
    departments = []
    for row in dept_totals.values():
        numbers = _row_numbers(row['required'], row['done'])
        gap = None
        if numbers['percent'] is not None and overall['percent'] is not None:
            gap = round(numbers['percent'] - overall['percent'], 1)
        departments.append({
            'name': row['name'], 'code': row['code'], 'instructors': row['instructors'],
            'gap': gap,
            'gap_text': (f"{gap:+g}" if gap is not None else '—'),
            'compare': ('above' if gap is not None and gap > 0.04 else
                        'below' if gap is not None and gap < -0.04 else
                        'same' if gap is not None else 'none'),
            **numbers,
        })
    departments.sort(key=lambda r: (-(r['percent'] if r['percent'] is not None else -1), r['name'].lower()))

    return {
        'overall': overall,
        'students_total': len(student_rows),
        'student_counts': counts,
        'students': student_rows,
        'instructors': instructors,
        'departments': departments,
        'sections': sections,
        'instructor_count': len(instructors),
    }


def sort_sections(rows, how):
    """Lowest completion first is the default, so the sections that need follow-up are on top."""
    key_name = lambda r: r['name'].lower()
    pct = lambda r: r['percent'] if r['percent'] is not None else -1
    if how == 'name':
        return sorted(rows, key=key_name)
    if how == 'high':
        return sorted(rows, key=lambda r: (-pct(r), key_name(r)))
    return sorted(rows, key=lambda r: (pct(r), key_name(r)))


def students_in_scope(rows, section_id=None, q=''):
    """The students of one section and/or whose name contains the search words (status is applied after)."""
    if section_id:
        rows = [r for r in rows if r['section_id'] == section_id]
    words = q.lower().split()
    if words:
        rows = [r for r in rows if all(w in r['name'].lower() for w in words)]
    return rows


def count_statuses(rows):
    counts = {COMPLETED: 0, PARTIAL: 0, NOT_STARTED: 0}
    for r in rows:
        counts[r['status']] += 1
    return counts


def apply_student_filter(rows, status):
    """Keep only the students with the chosen status (empty status = everybody)."""
    return [r for r in rows if r['status'] == status] if status else rows


def uncounted_students(period, filters):
    """
    Active students who are NOT in the numbers because they have no section, or their section has no
    teaching assignment in this semester. Shown as a data-quality note (only when nothing is filtered).
    """
    if filters.any_scope:
        return None
    covered = TeachingAssignment.objects.filter(
        school_year=period.school_year, semester=period.semester,
        teacher__role='instructor', teacher__is_active=True,
    ).values('section_id')
    students = User.objects.filter(role='student', is_active=True)
    return students.filter(section__isnull=True).count() + students.filter(
        section__isnull=False).exclude(section_id__in=covered).count()


def build(filters, today=None):
    """Everything the page needs. Always returns a dictionary (never raises for missing data)."""
    today = today or timezone.now().date()
    period, is_default = choose_period(filters, today)
    data = {
        'filters': filters,
        'options': period_options(),
        'period': None,
        'is_default': is_default,
        'stats': None,
        'uncounted': None,
    }
    if period is None:
        data['has_periods'] = EvaluationPeriod.objects.exists()
        return data
    data['has_periods'] = True
    data['period'] = period_block(period, today)
    data['stats'] = calculate(period, filters)
    data['uncounted'] = uncounted_students(period, filters)
    return data


# ─────────────────────────────────────────────
# Export (PDF / Excel) - uses the report makers that already exist in ai_insights/services/reports.py
# ─────────────────────────────────────────────
def _filter_words(filters):
    return ' · '.join([
        f"Department: {filters.department.name if filters.department else 'All'}",
        f"Section: {filters.section.name if filters.section else 'All'}",
        f"Instructor: {(filters.instructor.last_name + ', ' + filters.instructor.first_name) if filters.instructor else 'All'}",
    ])


def report_dict(data):
    """
    The same dictionary format as the AI reports, so the same PDF and Excel makers are reused.
    Only counts are exported: no student names, no ratings, no comments.
    """
    made = timezone.localtime().strftime('%b %d, %Y %H:%M')
    period = data['period']
    if period is None or data['stats'] is None:
        return {
            'title': 'Evaluation monitoring report', 'subtitle': f"Made {made}", 'landscape': False,
            'meta': [['Report generated', made]],
            'sections': [{'heading': 'Summary', 'headers': [], 'rows': [],
                          'text': 'There is no evaluation period for these filters, so there is nothing to report.'}],
        }
    stats, overall = data['stats'], data['stats']['overall']
    pct = lambda r: (f"{r['percent']:g}%" if r['percent'] is not None else '—')
    counts = stats['student_counts']
    return {
        'title': f"Evaluation monitoring · {period['words']}",
        'subtitle': f"Made {made}. Counted from the real student, teaching assignment and evaluation records.",
        'landscape': True,
        'meta': [
            ['Evaluation period', period['words']],
            ['Dates', f"{period['start']:%b %d, %Y} to {period['end']:%b %d, %Y}"],
            ['Status', f"{period['status_label']} ({period['timing']})"],
            ['Filters', _filter_words(data['filters'])],
        ],
        'sections': [
            {'heading': 'Overall progress',
             'text': ('Required = every active student evaluates every active instructor who teaches the '
                      "student's section in this semester. An instructor with 2 subjects in one section counts once."),
             'headers': ['Required', 'Completed', 'Remaining', 'Completion'],
             'rows': [[overall['required'], overall['completed'], overall['remaining'], pct(overall)]]},
            {'heading': 'Students by status',
             'headers': ['Completed', 'Partially completed', 'Not started', 'Students counted'],
             'rows': [[counts[COMPLETED], counts[PARTIAL], counts[NOT_STARTED], stats['students_total']]]},
            {'heading': 'Departments',
             'text': "Department = the instructor's department. 'Compared with school' is in percentage points.",
             'headers': ['Department', 'Instructors', 'Required', 'Completed', 'Remaining', 'Completion', 'Compared with school'],
             'rows': [[d['name'], d['instructors'], d['required'], d['completed'], d['remaining'], pct(d), d['gap_text']]
                      for d in stats['departments']]},
            {'heading': 'Sections',
             'text': 'Lowest completion first. Student columns count students, not evaluations.',
             'headers': ['Section', 'Department', 'Students', 'Completed', 'Partially', 'Not started',
                         'Required', 'Evaluations done', 'Completion'],
             'rows': [[x['name'], x['department'], x['students'], x['completed_students'], x['partial_students'],
                       x['not_started_students'], x['required'], x['completed'], pct(x)]
                      for x in sort_sections(stats['sections'], 'low')]},
            {'heading': 'Instructors',
             'text': 'Lowest completion first.',
             'headers': ['Instructor', 'Department', 'Sections', 'Required', 'Completed', 'Remaining', 'Completion'],
             'rows': [[i['name'], i['department'], i['sections'], i['required'], i['completed'], i['remaining'], pct(i)]
                      for i in stats['instructors']]},
        ],
    }