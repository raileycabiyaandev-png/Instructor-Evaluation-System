# academics/views.py
#
# Admin-only pages for Academic Management:
# Departments, Subjects, Sections (+ their students) and Teaching Assignments.

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

# Reuse the helpers that already exist in the project (no duplicates)
from evaluation.views import admin_required, no_cache
from users.models import User

from .forms import DepartmentForm, SectionForm, SubjectForm, TeachingAssignmentForm
from .models import Department, Section, Subject, TeachingAssignment
from .utils import (
    current_semester,
    decode_semester,
    encode_semester,
    semester_label,
    semester_options,
)


# ─────────────────────────────────────────────
# SMALL HELPERS (shared by all four screens)
# ─────────────────────────────────────────────

def _edit_page(request, form, title, subtitle, back_url_name):
    return render(request, 'academics/form_page.html', {
        'form': form,
        'title': title,
        'subtitle': subtitle,
        'back_url': back_url_name,
    })


def _safe_delete(request, obj, label, redirect_to):
    """Delete only when nothing depends on it; otherwise explain why."""
    try:
        obj.delete()
        messages.success(request, f"{label} deleted.")
    except ProtectedError:
        messages.error(
            request,
            f"{label} cannot be deleted because other records still use it. "
            f"Deactivate it instead."
        )
    return redirect(redirect_to)


def _toggle(request, obj, label, redirect_to):
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    state = 'activated' if obj.is_active else 'deactivated'
    messages.success(request, f"{label} {state}.")
    return redirect(redirect_to)


# ─────────────────────────────────────────────
# DEPARTMENTS
# ─────────────────────────────────────────────

@login_required
@no_cache
@admin_required
def department_list(request):
    form = DepartmentForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Department created.")
        return redirect('department_list')

    departments = Department.objects.annotate(
        subject_total=Count('subjects', distinct=True),
        section_total=Count('sections', distinct=True),
        teacher_total=Count('users', distinct=True),
    )

    return render(request, 'academics/department_list.html', {
        'form': form,
        'departments': departments,
    })


@login_required
@no_cache
@admin_required
def department_update(request, pk):
    department = get_object_or_404(Department, pk=pk)
    form = DepartmentForm(request.POST or None, instance=department)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Department updated.")
        return redirect('department_list')

    return _edit_page(request, form, 'Edit Department',
                      'Update the department details.', 'department_list')


@login_required
@admin_required
@require_POST
def department_toggle(request, pk):
    return _toggle(request, get_object_or_404(Department, pk=pk),
                   'Department', 'department_list')


@login_required
@admin_required
@require_POST
def department_delete(request, pk):
    return _safe_delete(request, get_object_or_404(Department, pk=pk),
                        'Department', 'department_list')


# ─────────────────────────────────────────────
# SUBJECTS
# ─────────────────────────────────────────────

@login_required
@no_cache
@admin_required
def subject_list(request):
    form = SubjectForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Subject created.")
        return redirect('subject_list')

    subjects = Subject.objects.select_related('department')

    return render(request, 'academics/subject_list.html', {
        'form': form,
        'subjects': subjects,
    })


@login_required
@no_cache
@admin_required
def subject_update(request, pk):
    subject = get_object_or_404(Subject, pk=pk)
    form = SubjectForm(request.POST or None, instance=subject)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Subject updated.")
        return redirect('subject_list')

    return _edit_page(request, form, 'Edit Subject',
                      'Update the subject and its department.', 'subject_list')


@login_required
@admin_required
@require_POST
def subject_toggle(request, pk):
    return _toggle(request, get_object_or_404(Subject, pk=pk),
                   'Subject', 'subject_list')


@login_required
@admin_required
@require_POST
def subject_delete(request, pk):
    return _safe_delete(request, get_object_or_404(Subject, pk=pk),
                        'Subject', 'subject_list')


# ─────────────────────────────────────────────
# SECTIONS
# ─────────────────────────────────────────────

@login_required
@no_cache
@admin_required
def section_list(request):
    form = SectionForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Section created.")
        return redirect('section_list')

    sections = Section.objects.select_related('department').annotate(
        student_total=Count('students', distinct=True),
    )

    return render(request, 'academics/section_list.html', {
        'form': form,
        'sections': sections,
    })


@login_required
@no_cache
@admin_required
def section_update(request, pk):
    section = get_object_or_404(Section, pk=pk)
    form = SectionForm(request.POST or None, instance=section)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Section updated.")
        return redirect('section_list')

    return _edit_page(request, form, 'Edit Section',
                      'Update the section details.', 'section_list')


@login_required
@admin_required
@require_POST
def section_toggle(request, pk):
    return _toggle(request, get_object_or_404(Section, pk=pk),
                   'Section', 'section_list')


@login_required
@admin_required
@require_POST
def section_delete(request, pk):
    return _safe_delete(request, get_object_or_404(Section, pk=pk),
                        'Section', 'section_list')


@login_required
@no_cache
@admin_required
def section_students(request, pk):
    """See the students of one section, add students to it, or remove them."""
    section = get_object_or_404(Section.objects.select_related('department'), pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            ids = request.POST.getlist('students')

            # Only real, active students of the SAME program, with no section yet
            to_add = User.objects.filter(
                pk__in=ids,
                role='student',
                is_active=True,
                program=section.program,
                section__isnull=True,
            )
            added = to_add.update(section=section)

            if added:
                messages.success(request, f"{added} student(s) added to {section.name}.")
            else:
                messages.warning(request, "No student was added. Please tick at least one student.")

        elif action == 'remove':
            removed = User.objects.filter(
                pk=request.POST.get('student'),
                role='student',
                section=section,
            ).update(section=None)

            if removed:
                messages.success(request, f"Student removed from {section.name}.")

        return redirect('section_students', pk=section.pk)

    members = section.students.order_by('last_name', 'first_name')

    available = User.objects.filter(
        role='student',
        is_active=True,
        program=section.program,
        section__isnull=True,
    ).order_by('last_name', 'first_name')

    return render(request, 'academics/section_students.html', {
        'section': section,
        'members': members,
        'available': available,
    })


# ─────────────────────────────────────────────
# TEACHING ASSIGNMENTS
# ─────────────────────────────────────────────

@login_required
@no_cache
@admin_required
def assignment_list(request):
    form = TeachingAssignmentForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Teaching assignment saved.")
        return redirect(f"{request.path}?semester={form.cleaned_data['semester_choice']}")

    # Which semester are we looking at? Default = semester of the current Evaluation Period.
    options = semester_options()
    selected = request.GET.get('semester')

    if selected is None:
        current = current_semester()
        selected = encode_semester(*current) if current else ''

    assignments = TeachingAssignment.objects.select_related(
        'teacher', 'teacher__department', 'subject', 'section',
    )

    if selected and selected != 'all':
        school_year, semester = decode_semester(selected)
        assignments = assignments.filter(school_year=school_year, semester=semester)

    return render(request, 'academics/assignment_list.html', {
        'form': form,
        'assignments': assignments,
        'semester_filter': [(encode_semester(sy, sem), semester_label(sy, sem)) for sy, sem in options],
        'selected_semester': selected,
        'has_periods': bool(options),
    })


@login_required
@no_cache
@admin_required
def assignment_update(request, pk):
    assignment = get_object_or_404(TeachingAssignment, pk=pk)
    form = TeachingAssignmentForm(request.POST or None, instance=assignment)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Teaching assignment updated.")
        return redirect('assignment_list')

    return _edit_page(request, form, 'Edit Teaching Assignment',
                      'Change the teacher, subject, section or semester.', 'assignment_list')


@login_required
@admin_required
@require_POST
def assignment_delete(request, pk):
    return _safe_delete(request, get_object_or_404(TeachingAssignment, pk=pk),
                        'Teaching assignment', 'assignment_list')
