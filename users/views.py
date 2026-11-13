from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, F
import json
import random
from .forms import (
    LoginForm,
    StudentCreateForm,
    InstructorCreateForm, 
    StudentFirstLoginForm,
    InstructorFirstLoginForm,
    StudentUpdateForm,
    InstructorUpdateForm,
    StudentProfileForm,
    InstructorProfileForm,
)
from .models import User
from evaluation.models import Evaluation, EvaluationPeriod
from django.http import JsonResponse
from django.contrib.auth import update_session_auth_hash
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from urllib.parse import urlencode
from academics.utils import get_open_period, instructor_cards_for_student
from ai_insights import conf as ai_conf                 # AI rules (one place)
from ai_insights import selectors as ai_selectors       # reads SAVED AI results only
from ai_insights.models import ReportLog
from ai_insights.services import data as ai_data        # one period of one instructor (same numbers everywhere)
from . import dashboard_data                            # numbers for the admin dashboard
from django.utils.decorators import method_decorator
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.core.exceptions import PermissionDenied

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Avg, Count
from django.utils import timezone
from evaluation.models import Evaluation, EvaluationPeriod, QRCode
from django.db.models import Q


def no_cache(view_func): # prevent caching 
    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response
    return wrapper


def user_login(request):

    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():

        email = form.cleaned_data["username"]
        password = form.cleaned_data["password"]

        # STEP 1: Find the person by email (before asking Django to log them in)
        account = User.objects.filter(email__iexact=email).first()

        # STEP 2: If the password is correct AND the account is inactive,
        # stop right here and show the deactivated message
        if (
            account
            and account.check_password(password)
            and account.role in ["student", "instructor"]
            and account.is_active == False
        ):
            messages.error(
                request,
                "Your account has been deactivated. Please contact the Administrator."
            )
            return render(request, "users/login.html", {"form": form})

        # STEP 3: Normal login check for everyone else
        user = authenticate(request, username=email, password=password)

        if user is None:
            messages.error(request, "Invalid email or password.")
            return render(request, "users/login.html", {"form": form})

        login(request, user)

        if user.must_change_password and user.role in ["student", "instructor"]:
            return redirect("force_update")

        return redirect_user_by_role(user)

    return render(request, "users/login.html", {"form": form})


def redirect_user_by_role(user):

    if user.role == "admin":
        return redirect("admin_dashboard")

    elif user.role == "student":
        return redirect("student_dashboard")

    elif user.role == "instructor":
        return redirect("instructor_dashboard")

    return redirect("login")


@login_required
def force_update(request):

    user = request.user

    # 🚫 BLOCK ADMIN FROM FORCE UPDATE PAGE
    if user.role == 'admin':
        return redirect('admin_dashboard')

    # If user already updated password, redirect to dashboard
    if not user.must_change_password:

        if user.role == 'student':
            return redirect('student_dashboard')

        elif user.role == 'instructor':
            return redirect('instructor_dashboard')

        return redirect('login')

    # Choose correct form based on role
    if user.role == 'student':
        form_class = StudentFirstLoginForm
    else:
        form_class = InstructorFirstLoginForm

    form = form_class(request.POST or None, instance=user)

    if request.method == 'POST':

        if form.is_valid():

            updated_user = form.save()

            # Keep user session active after password change
            update_session_auth_hash(request, updated_user)

            # Mark password as updated
            updated_user.must_change_password = False
            updated_user.save()

            # Redirect after update
            if user.role == 'student':
                return redirect('student_dashboard')

            elif user.role == 'instructor':
                return redirect('instructor_dashboard')

            return redirect('login')

    return render(
        request,
        'users/force_update.html',
        {'form': form}
    )

# ADMIN DASHBOARD
@login_required
@no_cache
def admin_dashboard(request):

    if request.user.role != 'admin':
        return redirect('login')

    teachers = User.objects.filter(role='instructor')
    students = User.objects.filter(role='student')

    total_instructors = teachers.count()
    total_students = students.count()
    total_users = total_instructors + total_students
    total_evaluations = Evaluation.objects.count()

    data = []

    for teacher in teachers:

        evaluations = Evaluation.objects.filter(teacher=teacher)

        avg = evaluations.aggregate(
            teaching=Avg('teaching_effectiveness'),
            communication=Avg('communication_skills'),
            classroom=Avg('classroom_management'),
            punctuality=Avg('punctuality'),
            engagement=Avg('student_engagement')
        )

        total_teacher_evaluations = evaluations.count()

        overall = round(
            (
                (avg['teaching'] or 0) +
                (avg['communication'] or 0) +
                (avg['classroom'] or 0) +
                (avg['punctuality'] or 0) +
                (avg['engagement'] or 0)
            ) / 5,
            2
        )

        data.append({
            'teacher': teacher,
            'avg': avg,
            'overall': overall,
            'evaluation_count': total_teacher_evaluations
        })

    # SORT BEST TEACHERS
    ranked_teachers = sorted(
        data,
        key=lambda x: (
            x['overall'],
            x['evaluation_count']
        ),
        reverse=True
    )

    # BEST TEACHER
    best_teacher = ranked_teachers[0] if ranked_teachers else None

    # CHART DATA
    chart_data = []

    for i, item in enumerate(data):

        chart_data.append({
            "id": f"chart{i+1}",
            "values": [
                round(item['avg']['teaching'] or 0, 2),
                round(item['avg']['communication'] or 0, 2),
                round(item['avg']['classroom'] or 0, 2),
                round(item['avg']['punctuality'] or 0, 2),
                round(item['avg']['engagement'] or 0, 2),
            ]
        })

    # NEW: the numbers for the top of the dashboard (see users/dashboard_data.py)
    dash = dashboard_data.build()

    return render(request, 'users/admin_dashboard.html', {

        'dash': dash,
        'outlook': ai_selectors.department_outlook(),     # saved AI forecasts + real averages only

        'data': ranked_teachers,
        'chart_data': json.dumps(chart_data),

        'total_users': total_users,
        'total_instructors': total_instructors,
        'total_students': total_students,
        'total_evaluations': total_evaluations,

        'best_teacher': best_teacher,
    })

# CREATE STUDENT
@login_required
@no_cache
def create_student(request):
    if request.user.role != 'admin':
        return redirect('login')

    form = StudentCreateForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Student created successfully!")
        return redirect('create_student')

    students = User.objects.filter(role='student').select_related('section')

    return render(request, 'users/create_student.html', {
        'form': form,
        'students': students
    })

# CREATE INSTRUCTOR
@login_required
@no_cache
def create_instructor(request):

    if request.user.role != 'admin':
        return redirect('login')

    form = InstructorCreateForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            form.save()
            messages.success(request, "Instructor created successfully!")
            return redirect('create_instructor')
        else:
            print(form.errors)  # DEBUG (REMOVE LATER)

    instructors = User.objects.filter(role='instructor').select_related('department')

    return render(request, 'users/create_instructor.html', {
        'form': form,
        'instructors': instructors
    })

@login_required
@no_cache
def student_dashboard(request):
    """
    Student home page.

    Shows ONLY the instructors who teach the student's own section in the
    semester of the open Evaluation Period (both evaluated and not yet
    evaluated), the student's progress, and their own evaluation history.
    """
    student = request.user

    if student.role != 'student':
        return redirect('login')

    period = get_open_period()
    today = timezone.now().date()

    cards = []
    if period and student.section_id:
        cards = instructor_cards_for_student(student, period)

    # What should the page tell the student?
    if not student.section_id:
        state = 'no_section'          # admin has not placed the student in a section
    elif not period:
        state = 'no_period'           # no open evaluation period
    elif not cards:
        state = 'no_assignments'      # section has no teaching assignment this semester
    else:
        state = 'ready'

    pending_cards = [c for c in cards if not c['is_evaluated']]
    evaluated_cards = [c for c in cards if c['is_evaluated']]
    total = len(cards)

    history = (
        Evaluation.objects
        .filter(student=student)
        .select_related('teacher', 'period')
        .order_by('-created_at')[:50]
    )

    return render(request, 'users/student_dashboard.html', {
        'state': state,
        'active_period': period,
        'days_left': (period.end_date - today).days if period else None,

        'pending_cards': pending_cards,
        'evaluated_cards': evaluated_cards,
        'total_instructors': total,
        'evaluated_count': len(evaluated_cards),
        'pending_count': len(pending_cards),
        'progress_percent': round(len(evaluated_cards) / total * 100) if total else 0,

        'history': history,
    })

# ─────────────────────────────────────────────
# HELPERS FOR TERM ORDER AND AVERAGES
# ─────────────────────────────────────────────

TERM_ORDER = {
    'prelims': 0,
    'midterm': 1,
    'prefinals': 2,
    'finals': 3,
}

TERM_LABELS = {
    'prelims': 'Prelims',
    'midterm': 'Midterm',
    'prefinals': 'Pre-Finals',
    'finals': 'Finals',
}


def period_sort_key(period):
    """Oldest -> newest: school year, then semester, then real term order."""
    return (
        period.school_year,
        period.semester,
        TERM_ORDER.get(period.term, 99),
    )


def average_rating(evaluations):
    """Average of the 5 categories for a queryset of evaluations."""
    data = evaluations.aggregate(
        teaching=Avg('teaching_effectiveness'),
        communication=Avg('communication_skills'),
        management=Avg('classroom_management'),
        punctuality=Avg('punctuality'),
        engagement=Avg('student_engagement'),
    )
    return round(
        (
            (data['teaching'] or 0) +
            (data['communication'] or 0) +
            (data['management'] or 0) +
            (data['punctuality'] or 0) +
            (data['engagement'] or 0)
        ) / 5,
        2
    )

MIN_RESPONSES = 3
# Same number the AI uses, so the progress bar and the forecast always agree.
FORECAST_MIN_PERIODS = ai_conf.MIN_PERIODS


def _finished_q(today):
    """
    One rule for "this period is finished" (so every teacher page agrees):
      - its end date has passed, OR
      - it has started but is not open any more (the admin closed it).
    """
    return Q(end_date__lt=today) | Q(is_active=False, start_date__lte=today)


def _period_state(period, today):
    """'upcoming' (not started), 'open' (students can evaluate) or 'finished'."""
    if period.is_active and period.end_date >= today:
        return "open"
    if not period.is_active and period.start_date > today:
        return "upcoming"
    return "finished"


def _period_history(instructor, finished_periods):
    """
    Real average rating of this teacher in each FINISHED period, oldest first.
    Periods without any response are skipped.
    """
    rows = []
    previous = None

    for period in finished_periods:
        evaluations = Evaluation.objects.filter(teacher=instructor, period=period)
        responses = evaluations.count()
        if responses == 0:
            continue

        rating = average_rating(evaluations)
        diff = None if previous is None else round(rating - previous, 2)

        rows.append({
            "period": period,
            "label": _period_label(period),
            "rating": rating,
            "responses": responses,
            "diff": diff,
            "trend": None if diff is None else _trend(diff),
            "diff_text": "" if diff is None else f"{diff:+.2f}",
        })
        previous = rating

    return rows


def _build_notifications(instructor, current_period, history, today):
    """
    Short messages made from REAL data (nothing is stored or invented).
    There is no "read / unread" yet because that needs a new database table.
    """
    items = []

    if current_period:
        days = (current_period.end_date - today).days
        when = "today" if days <= 0 else f"in {days} day(s)"
        items.append({
            "kind": "info",
            "icon": "bi-broadcast",
            "title": "An evaluation is open",
            "text": f"{_period_label(current_period)} closes {when} "
                    f"({current_period.end_date:%b %d, %Y}). Students can rate you until then.",
        })

    if history:
        last = history[-1]
        items.append({
            "kind": "success",
            "icon": "bi-check2-circle",
            "title": "Latest final results",
            "text": f"{last['label']}: {last['rating']:.2f} / 5 from "
                    f"{last['responses']} response(s). These scores are final.",
        })

        if last["diff"] is not None:
            if last["diff"] > 0:
                kind, icon, word = "success", "bi-graph-up-arrow", f"went up by {last['diff']:.2f}"
            elif last["diff"] < 0:
                kind, icon, word = "warning", "bi-graph-down-arrow", f"went down by {abs(last['diff']):.2f}"
            else:
                kind, icon, word = "info", "bi-dash-circle", "did not change"
            items.append({
                "kind": kind,
                "icon": icon,
                "title": "Compared with the period before",
                "text": f"Your rating {word} compared with {history[-2]['label']}.",
            })

    if not QRCode.objects.filter(instructor=instructor).exists():
        items.append({
            "kind": "warning",
            "icon": "bi-qr-code",
            "title": "Your QR code is not ready",
            "text": "The administrator has not created your QR code yet. Please ask them to create it.",
        })

    return items

def _teacher_focus(instructor, today):
    """
    Which period the teacher pages talk about first.
      'open'   a period is open now and the teacher takes part in it (shown live, NOT final)
      'latest' no open period (or the teacher is not part of it): the newest finished period with answers
      'empty'  nothing to show yet
    Returns (period, state, finished_periods_with_answers_oldest_first, open_period_or_None)
    """
    open_period = get_open_period()
    finished = sorted(
        EvaluationPeriod.objects.filter(_finished_q(today), evaluations__teacher=instructor).distinct(),
        key=period_sort_key,
    )
    if open_period:
        takes_part = (Evaluation.objects.filter(teacher=instructor, period=open_period).exists()
                      or ai_data.expected_students(instructor, open_period) > 0)
        if takes_part or not finished:
            return open_period, 'open', finished, open_period
    if finished:
        return finished[-1], 'latest', finished, open_period
    return None, 'empty', finished, open_period


def _overview_chart(instructor, snap, live):
    """Progress-over-time line chart: real scores of finished periods (+ the open period so far)."""
    rows = ai_data.history_rows(instructor)[-8:]
    live_point = (snap['label'], snap['overall']) if live and snap and snap['has_data'] else None
    return ai_selectors.progress_chart([r['label'] for r in rows], [r['overall'] for r in rows], live=live_point)


@login_required
@no_cache
def instructor_dashboard(request):
    """
    Teacher Dashboard (own data only). Order of the page:
      current period -> responses -> average + indicator -> comparison -> strongest / weakest / needs attention
      -> charts -> forecast, AI feedback summary, AI suggestions, updates.
    No open evaluation: the newest finished period is shown instead.
    """
    if request.user.role != 'instructor':
        return redirect('login')

    instructor = request.user
    today = timezone.now().date()
    focus, state, finished, open_period = _teacher_focus(instructor, today)

    snap = ai_data.period_snapshot(instructor, focus) if focus else None
    live = state == 'open'
    ai = ai_selectors.teacher_dashboard_ai(instructor)
    history = _period_history(instructor, finished)

    # ?tab=profile opens the Profile view (set by the sidebar); ?section= opens one of the AI tabs
    active_tab = 'profile' if request.GET.get('tab') == 'profile' else 'dashboard'
    start_tab = request.GET.get('section', '')
    if start_tab not in ('forecast', 'feedback', 'suggestions', 'updates'):
        start_tab = 'feedback'

    return render(request, "users/instructor_dashboard.html", {
        "active_tab": active_tab,
        "state": state,
        "focus": focus,
        "open_period": open_period,
        "days_left": (open_period.end_date - today).days if open_period else None,
        "snap": snap,
        "live": live,
        "line_chart": _overview_chart(instructor, snap, live),
        "ai": ai,
        "start_tab": start_tab,
        "notifications": _build_notifications(instructor, open_period, history, today),
        "min_responses": MIN_RESPONSES,
    })

@login_required
@no_cache
def update_student(request, pk):
    if request.user.role != 'admin':
        return redirect('login')

    student = get_object_or_404(User, pk=pk, role='student')

    form = StudentUpdateForm(request.POST or None, instance=student)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Student updated successfully!")
        return redirect('create_student')

    return render(request, 'users/update_student.html', {
        'form': form,
        'student': student
    })

@login_required
@no_cache
def delete_student(request, pk):
    if request.user.role != 'admin':
        return redirect('login')

    student = get_object_or_404(User, pk=pk, role='student')

    if request.method == 'POST':
        student.delete()
        messages.success(request, "Student deleted successfully!")

    return redirect('create_student')

# users.views.py
@login_required
@no_cache
def chart_api(request):

    if request.user.role != 'admin':
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    teachers = User.objects.filter(role='instructor')

    chart_data = []

    for i, teacher in enumerate(teachers):

        avg = Evaluation.objects.filter(
            teacher=teacher
        ).aggregate(
            teaching=Avg('teaching_effectiveness'),
            communication=Avg('communication_skills'),
            classroom=Avg('classroom_management'),
            punctuality=Avg('punctuality'),
            engagement=Avg('student_engagement')
        )

        chart_data.append({
            "id": f"chart{i+1}",
            "values": [
                round(avg['teaching'] or 0, 2),
                round(avg['communication'] or 0, 2),
                round(avg['classroom'] or 0, 2),
                round(avg['punctuality'] or 0, 2),
                round(avg['engagement'] or 0, 2)
            ]
        })

    return JsonResponse(chart_data, safe=False)

@login_required
@no_cache
def update_instructor(request, pk):
    if request.user.role != 'admin':
        return redirect('login')

    instructor = get_object_or_404(User, pk=pk, role='instructor')

    if request.method == 'POST':
        form = InstructorUpdateForm(request.POST, instance=instructor)

        if form.is_valid():
            form.save()
            messages.success(request, "Instructor updated successfully.")
            return redirect('create_instructor')
    else:
        form = InstructorUpdateForm(instance=instructor)

    return render(request, 'users/instructor_update.html', {
        'form': form
    })

@login_required
@no_cache
def delete_instructor(request, pk):
    if request.user.role != 'admin':
        return redirect('login')

    instructor = get_object_or_404(User, pk=pk, role='instructor')

    if request.method == 'POST':
        instructor.delete()
        messages.success(request, "Instructor deleted successfully.")
        return redirect('create_instructor')

    return redirect('create_instructor')

@login_required
@no_cache
def student_update_profile(request):

    if request.user.role != 'student':
        return redirect('login')

    if request.method == "POST":

        form = StudentProfileForm(request.POST, instance=request.user)

        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)

    return redirect(reverse("student_dashboard") + "#profile")


@login_required
@no_cache
def student_change_password(request):

    if request.user.role != 'student':
        return redirect('login')

    if request.method == "POST":

        form = PasswordChangeForm(request.user, request.POST)

        if form.is_valid():

            if form.cleaned_data['old_password'] == form.cleaned_data['new_password1']:
                messages.error(request, "Your new password must be different from your current password.")
            else:
                user = form.save()
                update_session_auth_hash(request, user)   # keep the student logged in
                messages.success(request, "Password changed successfully.")

        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)

    return redirect(reverse("student_dashboard") + "#profile")

@login_required
@no_cache
def instructor_update_profile(request):

    if request.user.role != 'instructor':
        return redirect('login')

    if request.method == "POST":

        form = InstructorProfileForm(request.POST, instance=request.user)

        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)

    # ?tab=profile so the message is shown on the Profile view
    return redirect(reverse("instructor_dashboard") + "?tab=profile")

@login_required
@require_POST
def deactivate_student(request, pk):

    if request.user.role != "admin":
        return redirect("login")

    student = get_object_or_404(
        User,
        pk=pk,
        role="student"
    )

    student.is_active = False
    student.save()

    messages.success(
        request,
        "Student account has been deactivated."
    )

    return redirect("create_student")

@login_required
@require_POST
def activate_student(request, pk):

    if request.user.role != "admin":
        return redirect("login")

    student = get_object_or_404(
        User,
        pk=pk,
        role="student"
    )

    student.is_active = True
    student.save()

    messages.success(
        request,
        "Student account has been activated."
    )

    return redirect("create_student")

@login_required
@require_POST
def deactivate_instructor(request, pk):

    if request.user.role != "admin":
        return redirect("login")

    instructor = get_object_or_404(User, pk=pk, role="instructor")

    instructor.is_active = False
    instructor.save()

    messages.success(request, "Instructor account has been deactivated.")

    return redirect("create_instructor")


@login_required
@require_POST
def activate_instructor(request, pk):

    if request.user.role != "admin":
        return redirect("login")

    instructor = get_object_or_404(User, pk=pk, role="instructor")

    instructor.is_active = True
    instructor.save()

    messages.success(request, "Instructor account has been activated.")

    return redirect("create_instructor")

# ─────────────────────────────────────────────────────────────
# Put this in the SAME views.py where your instructor dashboard
# view lives (assumed: users/views.py).
# ─────────────────────────────────────────────────────────────
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count
from django.shortcuts import render
from django.utils import timezone

from evaluation.models import Evaluation, EvaluationPeriod

# The five required categories: (database field, label shown on screen)
CATEGORIES = [
    ("teaching_effectiveness", "Teaching Effectiveness"),
    ("communication_skills", "Communication Skills"),
    ("classroom_management", "Classroom Management"),
    ("punctuality", "Punctuality"),
    ("student_engagement", "Student Engagement"),
]


def _score_level(value):
    """Same color rule as the dashboard: 4.5+ high, 3.5+ mid, else low."""
    if value >= 4.5:
        return "high"
    if value >= 3.5:
        return "mid"
    return "low"


def _trend(diff):
    if diff > 0:
        return "up"
    if diff < 0:
        return "down"
    return "same"


def _period_label(period):
    return (
        f"{period.get_term_display()} · "
        f"{period.get_semester_display()}, S.Y. {period.school_year}"
    )


def _get_scores(teacher, period):
    """
    Combined (averaged) scores for ONE teacher in ONE period.
    Only counts and averages are read. The student column is never
    selected, so student identity cannot leak into the page.
    Returns None when there are no responses.
    """
    agg = Evaluation.objects.filter(teacher=teacher, period=period).aggregate(
        total=Count("id"),
        **{field: Avg(field) for field, _ in CATEGORIES},
    )
    if not agg["total"]:
        return None

    # Every category has equal weight (the models have no weight setting yet).
    overall = sum(agg[field] for field, _ in CATEGORIES) / len(CATEGORIES)
    return {
        "total": agg["total"],
        "overall": round(overall, 2),
        "categories": {field: round(agg[field], 2) for field, _ in CATEGORIES},
    }


RESULT_TABS = ("overview", "history", "ai", "comments")


def _paged(items, request, tab, active, per_page):
    """Page of `items` (only the tab being viewed uses ?page=, the others stay on page 1)."""
    number = request.GET.get("page") if tab == active else 1
    return Paginator(items, per_page).get_page(number)


@login_required
@no_cache
def instructor_evaluation_results(request):
    """
    Evaluation Results (own data only): the same numbers as the Dashboard, for any period you choose,
    plus the complete history, the AI analysis and the anonymous comments.
    """
    teacher = request.user
    if teacher.role != "instructor":
        raise PermissionDenied

    today = timezone.now().date()
    focus, _focus_state, _finished, open_period = _teacher_focus(teacher, today)

    all_periods = EvaluationPeriod.objects.all()
    school_years = list(all_periods.values_list("school_year", flat=True).distinct().order_by("-school_year"))
    default_period = focus or all_periods.order_by("-start_date").first()

    selected = {
        "school_year": request.GET.get("school_year") or (default_period.school_year if default_period else ""),
        "semester": request.GET.get("semester") or (default_period.semester if default_period else ""),
        "term": request.GET.get("term") or (default_period.term if default_period else ""),
    }
    tab = request.GET.get("tab", "overview")
    if tab not in RESULT_TABS:
        tab = "overview"

    period = None
    if not all_periods.exists():
        state = "no_periods"
    else:
        period = all_periods.filter(**selected).first()
        if period is None:
            state = "not_found"
        elif _period_state(period, today) == "upcoming":
            state = "not_open"
        else:
            state = "open" if _period_state(period, today) == "open" else "finished"

    snap = ai_data.period_snapshot(teacher, period) if period and state in ("open", "finished") else None
    if snap is not None and not snap["has_data"]:
        state = "no_responses"

    live = state == "open"
    section = request.GET.get("section", "feedback")           # which AI sub-tab (drawn by the server)
    if section not in ("feedback", "suggestions", "forecast"):
        section = "feedback"
    context = {
        "active_tab": "results", "tab": tab, "section": section, "tabs": RESULT_TABS, "state": state, "live": live,
        "period": period, "period_label": _period_label(period) if period else "",
        "school_years": school_years, "semester_choices": EvaluationPeriod.SEMESTER_CHOICES,
        "term_choices": EvaluationPeriod.TERM_CHOICES, "selected": selected,
        "snap": snap, "min_responses": MIN_RESPONSES,
        "keep": urlencode({**selected, "tab": tab}),
        "keep_filters": urlencode(selected),
        "days_left": (period.end_date - today).days if state == "open" else None,
    }

    if snap and snap["has_data"]:
        if tab == "overview":
            rows = ai_data.history_rows(teacher, upto=period)[-8:]
            live_point = (snap["label"], snap["overall"]) if live else None
            context["line_chart"] = ai_selectors.progress_chart(
                [r["label"] for r in rows if not live or r["period"].pk != period.pk],
                [r["overall"] for r in rows if not live or r["period"].pk != period.pk], live=live_point)

        if tab == "history":
            rows = ai_data.history_rows(teacher)                   # complete history, oldest first
            for r in rows:
                r["short"] = ai_selectors._short_label(r["label"])   # short name so the table fits
            context["pager"] = _paged(list(reversed(rows)), request, tab, "history", 8)
            context["history_chart"] = charts_line_for_categories(rows)
            context["history_total"] = len(rows)

        if tab == "ai":
            dash_ai = ai_selectors.teacher_dashboard_ai(teacher)
            context["ai"] = dash_ai
            context["period_ai"] = (ai_selectors.teacher_results_ai(teacher, period) if state == "finished" else None)

        if tab == "comments":
            feedback = _get_feedback(teacher, period, snap["responses"]) if state == "finished" else None
            context["feedback"] = feedback
            if feedback and not feedback["hidden"]:
                context["pager"] = _paged(feedback["comments"], request, tab, "comments", 10)
    return render(request, "users/instructor_evaluation_results.html", context)


def charts_line_for_categories(rows):
    """Line chart with one line per category over the complete history."""
    from ai_insights import charts as ai_charts
    if not rows:
        return None
    return ai_charts.line_chart(
        [ai_selectors._short_label(r["label"]) for r in rows],
        [{"name": label, "values": [r["categories"][field] for r in rows]} for field, label in CATEGORIES])


@login_required
@no_cache
def instructor_reports(request):
    """Reports page: choose a finished period and download the PDF or Excel report (own data only)."""
    teacher = request.user
    if teacher.role != "instructor":
        raise PermissionDenied

    own = [p for p in ai_data.finished_periods() if Evaluation.objects.filter(teacher=teacher, period=p).exists()]
    options = [{"id": p.pk, "label": _period_label(p)} for p in reversed(own)]
    chosen = next((p for p in own if str(p.pk) == request.GET.get("period", "")), own[-1] if own else None)

    snap = ai_data.period_snapshot(teacher, chosen) if chosen else None
    release = ai_selectors.latest_release()
    recent = ReportLog.objects.filter(user=teacher, kind="my_report")
    return render(request, "users/instructor_reports.html", {
        "active_tab": "reports", "options": options, "chosen": chosen, "snap": snap,
        "has_ai": release is not None,
        "pager": Paginator(recent, 5).get_page(request.GET.get("page")),
        "keep": urlencode({"period": chosen.pk}) if chosen else "",
    })


def _get_feedback(teacher, period, total_responses):
    """
    Written comments for ONE teacher in ONE finished period.

    Privacy rules:
      - only the comment text is read (never the student, never the date),
      - the comments are mixed in a fixed random order, so the order cannot
        reveal who sent them first,
      - nothing is shown when fewer than MIN_RESPONSES students evaluated.
    """
    if total_responses < MIN_RESPONSES:
        return {"hidden": True, "needed": MIN_RESPONSES, "comments": [], "count": 0}

    texts = [
        text.strip()
        for text in Evaluation.objects.filter(teacher=teacher, period=period)
        .exclude(comment="")
        .values_list("comment", flat=True)
        if text and text.strip()
    ]
    random.Random(f"{teacher.pk}-{period.pk}").shuffle(texts)

    return {
        "hidden": False,
        "needed": MIN_RESPONSES,
        "comments": texts,
        "count": len(texts),
        "total": total_responses,
    }


@login_required
@no_cache
def instructor_qr(request):
    """The teacher's OWN QR code (read only). Only the admin creates it."""
    if request.user.role != "instructor":
        raise PermissionDenied

    qr = QRCode.objects.filter(instructor=request.user).first()

    # The record can exist while the image file is missing from the disk
    file_ok = bool(qr and qr.qr_image and qr.qr_image.storage.exists(qr.qr_image.name))

    return render(request, "users/instructor_qr.html", {
        "active_tab": "qr",
        "qr": qr if file_ok else None,
        "qr_missing_file": bool(qr) and not file_ok,
        "open_period": get_open_period(),
    })