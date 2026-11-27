# ai_insights/filters.py
#
# ONE place that reads and CHECKS the filters of the admin AI pages
# (academic term, evaluation period, department, instructor).
#
# Safety rules:
#   * Values come from the web address (GET), so they are never trusted.
#   * Every value is checked against the database. A wrong or made-up value
#     is simply ignored (the page then shows "all").
#   * Only ACTIVE INSTRUCTORS can be chosen. Students and admins can never appear.

from dataclasses import dataclass, field
from urllib.parse import urlencode

from academics.models import Department
from users.models import User

from .services.data import finished_periods


@dataclass
class Filters:
    school_year: str = ''          # "2026-2027" or "" (all school years)
    semester: str = ''             # "1st" / "2nd" or "" (both)
    period: object = None          # EvaluationPeriod or None
    department: object = None      # Department or None
    instructor: object = None      # User or None
    # choices shown in the drop-down lists
    year_options: list = field(default_factory=list)
    semester_options: list = field(default_factory=list)
    period_options: list = field(default_factory=list)
    department_options: list = field(default_factory=list)
    instructor_options: list = field(default_factory=list)

    def querystring(self, **extra):
        """The CURRENT filters as a web-address part, e.g. 'department=2&period=5'."""
        values = {
            'school_year': self.school_year,
            'semester': self.semester,
            'period': self.period.pk if self.period else '',
            'department': self.department.pk if self.department else '',
            'instructor': self.instructor.pk if self.instructor else '',
        }
        values.update(extra)
        return urlencode({k: v for k, v in values.items() if v not in ('', None)})

    @property
    def term(self):
        """Old name: "2026-2027|1st" when BOTH school year and semester are chosen, else ''."""
        return f"{self.school_year}|{self.semester}" if self.school_year and self.semester else ''

    def matches(self, period):
        """True when `period` is inside the chosen school year / semester (each one optional)."""
        return ((not self.school_year or period.school_year == self.school_year)
                and (not self.semester or period.semester == self.semester))

    @property
    def any_active(self):
        return bool(self.school_year or self.semester or self.period or self.department or self.instructor)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def term_value(period):
    return f"{period.school_year}|{period.semester}"


def parse_filters(params):
    """`params` is request.GET. Returns a checked Filters object."""
    periods = finished_periods()                           # oldest -> newest

    # ---- school year and semester (two separate lists, easier to understand than one combined list)
    years = sorted({p.school_year for p in periods}, reverse=True)
    semesters = []
    for p in periods:
        if p.semester not in [v for v, _ in semesters]:
            semesters.append((p.semester, p.get_semester_display()))
    school_year = params.get('school_year', '')
    semester = params.get('semester', '')
    old = params.get('term', '')                           # older links: "2026-2027|1st"
    if old and not school_year and not semester and '|' in old:
        school_year, semester = old.split('|', 1)
    if school_year not in years:
        school_year = ''
    if semester not in [v for v, _ in semesters]:
        semester = ''

    # ---- evaluation period (finished only), limited by the chosen year + semester
    in_term = [p for p in periods if (not school_year or p.school_year == school_year)
               and (not semester or p.semester == semester)]
    period_options = [(p.pk, f"{p.get_term_display()} · {p.get_semester_display()}, S.Y. {p.school_year}")
                      for p in reversed(in_term)]
    period = None
    wanted = _int(params.get('period'))
    if wanted is not None:
        period = next((p for p in in_term if p.pk == wanted), None)

    # ---- department
    departments = list(Department.objects.order_by('name'))
    department = None
    wanted = _int(params.get('department'))
    if wanted is not None:
        department = next((d for d in departments if d.pk == wanted), None)

    # ---- instructor (only active instructors, limited by the department)
    teachers = User.objects.filter(role='instructor', is_active=True)
    if department:
        teachers = teachers.filter(department=department)
    teachers = list(teachers.order_by('last_name', 'first_name'))
    instructor = None
    wanted = _int(params.get('instructor'))
    if wanted is not None:
        instructor = next((t for t in teachers if t.pk == wanted), None)

    return Filters(
        school_year=school_year, semester=semester, period=period, department=department, instructor=instructor,
        year_options=years, semester_options=semesters,
        period_options=period_options,
        department_options=[(d.pk, d.name) for d in departments],
        instructor_options=[(t.pk, f"{t.last_name}, {t.first_name}") for t in teachers],
    )


def filtered_instructors(filters):
    """Active instructors that match the department / instructor filters (with their department loaded)."""
    qs = User.objects.filter(role='instructor', is_active=True).select_related('department')
    if filters.department:
        qs = qs.filter(department=filters.department)
    if filters.instructor:
        qs = qs.filter(pk=filters.instructor.pk)
    return qs.order_by('last_name', 'first_name')