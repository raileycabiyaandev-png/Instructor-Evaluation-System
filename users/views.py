from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, F
import json
from .forms import (
    LoginForm,
    StudentCreateForm,
    InstructorCreateForm, 
    StudentFirstLoginForm,
    InstructorFirstLoginForm,
    StudentUpdateForm,
    InstructorUpdateForm,
)
from .models import User
from evaluation.models import Evaluation, EvaluationPeriod
from django.http import JsonResponse
from django.contrib.auth import update_session_auth_hash
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.core.exceptions import PermissionDenied

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Avg, Count
from evaluation.models import Evaluation, EvaluationPeriod


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

    return render(request, 'users/admin_dashboard.html', {

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
        return redirect('create_student')

    students = User.objects.filter(role='student')

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

    instructors = User.objects.filter(role='instructor')

    return render(request, 'users/create_instructor.html', {
        'form': form,
        'instructors': instructors
    })

@login_required
@no_cache
def student_dashboard(request):

    if request.user.role != 'student':
        return redirect('login')

    active_period = EvaluationPeriod.objects.filter(is_active=True).first()

    if not active_period:
        return render(request, 'users/student_dashboard.html', {
            'active_period': None,
            'evaluated_teachers': [],
            'pending_teachers': []
        })

    evaluations = Evaluation.objects.filter(
        student=request.user,
        period=active_period
    )

    evaluated_ids = set(evaluations.values_list('teacher_id', flat=True))

    teachers = User.objects.filter(role='instructor')

    teacher_list = [
        {
            'teacher': t,
            'is_evaluated': t.id in evaluated_ids
        }
        for t in teachers
    ]

    evaluated_teachers = [t for t in teacher_list if t['is_evaluated']]

    return render(request, 'users/student_dashboard.html', {
        'active_period': active_period,
        'evaluated_teachers': evaluated_teachers,
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

@login_required
@no_cache
def instructor_dashboard(request):

    if request.user.role != 'instructor':
        return redirect('login')

    instructor = request.user

    # ─────────────────────────────────────────────
    # 1. ACTIVE TAB
    # ─────────────────────────────────────────────
    active_tab = request.GET.get('tab', 'dashboard')

    # ─────────────────────────────────────────────
    # 2. FILTERS
    # ─────────────────────────────────────────────
    selected_school_year = request.GET.get('school_year', '')
    selected_semester = request.GET.get('semester', '')
    selected_term = request.GET.get('term', '')

    # ─────────────────────────────────────────────
    # 3. CURRENT ACTIVE PERIOD
    # ─────────────────────────────────────────────
    current_period = (
        EvaluationPeriod.objects
        .filter(is_active=True)
        .first()
    )

    # ─────────────────────────────────────────────
    # 4. DASHBOARD: CURRENT PERIOD RESULTS
    # ─────────────────────────────────────────────
    current_period_evaluations = Evaluation.objects.none()

    current_total_responses = 0
    current_category_averages = {}
    current_overall_average = None
    current_chart_data = {}

    if current_period:
        current_period_evaluations = Evaluation.objects.filter(
            teacher=instructor,
            period=current_period
        )

        current_total_responses = current_period_evaluations.count()

        if current_total_responses > 0:

            current_category_data = current_period_evaluations.aggregate(
                teaching=Avg('teaching_effectiveness'),
                communication=Avg('communication_skills'),
                management=Avg('classroom_management'),
                punctuality=Avg('punctuality'),
                engagement=Avg('student_engagement'),
            )

            current_category_averages = {
                "Teaching Effectiveness": round(
                    current_category_data['teaching'] or 0, 2
                ),
                "Communication Skills": round(
                    current_category_data['communication'] or 0, 2
                ),
                "Classroom Management": round(
                    current_category_data['management'] or 0, 2
                ),
                "Punctuality": round(
                    current_category_data['punctuality'] or 0, 2
                ),
                "Student Engagement": round(
                    current_category_data['engagement'] or 0, 2
                ),
            }

            current_overall_average = round(
                sum(current_category_averages.values()) / 5,
                2
            )

            current_chart_data = {
                "labels": list(current_category_averages.keys()),
                "values": list(current_category_averages.values()),
            }

    # ─────────────────────────────────────────────
    # 5. DASHBOARD STATE
    # ─────────────────────────────────────────────
    if current_period:
        if current_total_responses > 0:
            dashboard_state = "current_results"
        else:
            dashboard_state = "no_current_ratings"
    else:
        dashboard_state = "recent_performance"

    # ─────────────────────────────────────────────
    # 6. COMPLETED PERIODS (FIXED TERM ORDER)
    #    Same shape as your original chart_trend,
    #    so the Results tab keeps working.
    # ─────────────────────────────────────────────
    completed_list = sorted(
        EvaluationPeriod.objects
        .filter(evaluations__teacher=instructor, is_active=False)
        .distinct(),
        key=period_sort_key
    )

    trend_labels = []
    trend_values = []

    for period in completed_list:

        period_average = average_rating(
            Evaluation.objects.filter(teacher=instructor, period=period)
        )

        trend_labels.append(
            f"{period.school_year} "
            f"{period.get_semester_display()} "
            f"{period.get_term_display()}"
        )

        trend_values.append(period_average)

    chart_trend = {
        "labels": trend_labels,
        "values": trend_values,
    }

    # ─────────────────────────────────────────────
    # 7. MOST RECENT COMPLETED PERIOD
    # ─────────────────────────────────────────────
    recent_period = completed_list[-1] if completed_list else None

    if trend_values:
        recent_overall_average = trend_values[-1]
    else:
        recent_overall_average = None

    # ─────────────────────────────────────────────
    # 8. PERFORMANCE INDICATOR
    # ─────────────────────────────────────────────
    if len(trend_values) >= 2:

        latest = trend_values[-1]
        previous = trend_values[-2]
        diff = round(latest - previous, 2)

        if latest > previous:
            performance_status = f"Performance Improved (+{diff})"
            performance_icon = "bi-graph-up-arrow"

        elif latest < previous:
            performance_status = f"Performance Declined ({diff})"
            performance_icon = "bi-graph-down-arrow"

        else:
            performance_status = "Performance Stable"
            performance_icon = "bi-dash-circle"

    else:
        performance_status = "Not Enough Data Yet"
        performance_icon = "bi-dash-circle"

    # ─────────────────────────────────────────────
    # 8B. PERFORMANCE OVER TIME (NEW LINE GRAPH)
    #
    #    - Latest school year + semester only
    #    - Active (ongoing) term included
    #    - Always 4 terms on the bottom axis
    #    - Terms with no ratings are left empty
    # ─────────────────────────────────────────────
    candidate_periods = list(
        EvaluationPeriod.objects
        .filter(evaluations__teacher=instructor)
        .distinct()
    )

    if current_period and current_period not in candidate_periods:
        candidate_periods.append(current_period)

    graph_period_label = ""
    graph_labels = []
    graph_values = []
    graph_statuses = []
    graph_responses = []

    if candidate_periods:
        newest = max(candidate_periods, key=period_sort_key)

        graph_period_label = (
            f"S.Y. {newest.school_year} · {newest.get_semester_display()}"
        )

        periods_by_term = {
            p.term: p
            for p in EvaluationPeriod.objects.filter(
                school_year=newest.school_year,
                semester=newest.semester,
            )
        }
    else:
        periods_by_term = {}

    for term_key in sorted(TERM_ORDER, key=TERM_ORDER.get):

        graph_labels.append(TERM_LABELS[term_key])

        term_period = periods_by_term.get(term_key)

        if term_period is None:
            graph_values.append(None)
            graph_statuses.append("none")
            graph_responses.append(0)
            continue

        term_evaluations = Evaluation.objects.filter(
            teacher=instructor,
            period=term_period
        )
        response_count = term_evaluations.count()

        graph_responses.append(response_count)

        if response_count > 0:
            graph_values.append(average_rating(term_evaluations))
        else:
            graph_values.append(None)

        if term_period.is_active:
            graph_statuses.append("ongoing")
        elif response_count > 0:
            graph_statuses.append("completed")
        else:
            graph_statuses.append("none")

    rated_points = [
        (graph_labels[i], graph_values[i])
        for i in range(len(graph_values))
        if graph_values[i] is not None
    ]

    if len(rated_points) >= 2:
        prev_label, prev_value = rated_points[-2]
        last_label, last_value = rated_points[-1]
        graph_diff = round(last_value - prev_value, 2)

        if graph_diff > 0:
            graph_summary = {
                "css": "up",
                "icon": "bi-graph-up-arrow",
                "title": "Improving",
                "text": f"+{graph_diff} from {prev_label} to {last_label}",
            }
        elif graph_diff < 0:
            graph_summary = {
                "css": "down",
                "icon": "bi-graph-down-arrow",
                "title": "Declining",
                "text": f"{graph_diff} from {prev_label} to {last_label}",
            }
        else:
            graph_summary = {
                "css": "same",
                "icon": "bi-dash-circle",
                "title": "Steady",
                "text": f"No change from {prev_label} to {last_label}",
            }
    elif len(rated_points) == 1:
        graph_summary = {
            "css": "same",
            "icon": "bi-hourglass-split",
            "title": "First Rating",
            "text": "Only one term has ratings so far. A trend appears after the next term.",
        }
    else:
        graph_summary = {
            "css": "same",
            "icon": "bi-hourglass-split",
            "title": "No Ratings Yet",
            "text": "Your line will appear once students submit evaluations.",
        }

    performance_graph = {
        "labels": graph_labels,
        "values": graph_values,        # None becomes null in the browser
        "statuses": graph_statuses,    # completed / ongoing / none
        "responses": graph_responses,
        "period_label": graph_period_label,
        "summary": graph_summary,
        "has_data": len(rated_points) > 0,
    }

    # ─────────────────────────────────────────────
    # 9. EVALUATION RESULTS TAB
    # ─────────────────────────────────────────────
    result_evaluations = Evaluation.objects.filter(
        teacher=instructor
    )

    if selected_school_year:
        result_evaluations = result_evaluations.filter(
            period__school_year=selected_school_year
        )

    if selected_semester:
        result_evaluations = result_evaluations.filter(
            period__semester=selected_semester
        )

    if selected_term:
        result_evaluations = result_evaluations.filter(
            period__term=selected_term
        )

    result_category_data = result_evaluations.aggregate(
        teaching=Avg('teaching_effectiveness'),
        communication=Avg('communication_skills'),
        management=Avg('classroom_management'),
        punctuality=Avg('punctuality'),
        engagement=Avg('student_engagement'),
    )

    result_category_averages = {
        "Teaching Effectiveness": round(
            result_category_data['teaching'] or 0, 2
        ),
        "Communication Skills": round(
            result_category_data['communication'] or 0, 2
        ),
        "Classroom Management": round(
            result_category_data['management'] or 0, 2
        ),
        "Punctuality": round(
            result_category_data['punctuality'] or 0, 2
        ),
        "Student Engagement": round(
            result_category_data['engagement'] or 0, 2
        ),
    }

    result_total_responses = result_evaluations.count()

    if result_total_responses > 0:
        result_overall_average = round(
            sum(result_category_averages.values()) / 5,
            2
        )
    else:
        result_overall_average = None

    result_chart_data = {
        "labels": list(result_category_averages.keys()),
        "values": list(result_category_averages.values()),
    }

    # ─────────────────────────────────────────────
    # 10. FILTER DROPDOWN OPTIONS
    # ─────────────────────────────────────────────
    school_years = (
        EvaluationPeriod.objects
        .filter(evaluations__teacher=instructor)
        .values_list('school_year', flat=True)
        .distinct()
        .order_by('school_year')
    )

    # ─────────────────────────────────────────────
    # 11. SEND DATA TO TEMPLATE
    # ─────────────────────────────────────────────
    return render(
        request,
        "users/instructor_dashboard.html",
        {
            # Tab
            "active_tab": active_tab,

            # Current active period
            "current_period": current_period,

            # Dashboard state
            "dashboard_state": dashboard_state,

            # Current evaluation
            "current_total_responses": current_total_responses,
            "current_category_averages": current_category_averages,
            "current_overall_average": current_overall_average,
            "current_chart_data": current_chart_data,

            # Recent completed evaluation
            "recent_period": recent_period,
            "recent_overall_average": recent_overall_average,

            # Performance history (original, used by Results tab)
            "chart_trend": chart_trend,
            "performance_status": performance_status,
            "performance_icon": performance_icon,

            # NEW: Performance Over Time line graph
            "performance_graph": performance_graph,

            # Evaluation Results tab
            "category_averages": result_category_averages,
            "overall_average": result_overall_average,
            "total_responses": result_total_responses,
            "chart_data": result_chart_data,

            # Filters
            "school_years": school_years,
            "selected_school_year": selected_school_year,
            "selected_semester": selected_semester,
            "selected_term": selected_term,
        }
    )

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

    if request.method == "POST":

        request.user.first_name = request.POST.get("first_name")
        request.user.middle_name = request.POST.get("middle_name")
        request.user.last_name = request.POST.get("last_name")
        request.user.email = request.POST.get("email")

        request.user.save()

        messages.success(request, "Profile updated successfully.")

    return redirect(reverse("student_dashboard") + "#profile")

@login_required
@no_cache
def instructor_update_profile(request):
    if request.method == "POST":
        request.user.first_name = request.POST.get("first_name")
        request.user.last_name  = request.POST.get("last_name")
        request.user.email      = request.POST.get("email")
        request.user.save()
        messages.success(request, "Profile updated successfully.")
    return redirect("instructor_dashboard")

@login_required
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


@login_required
def instructor_evaluation_results(request):
    teacher = request.user
    if teacher.role != "instructor":
        raise PermissionDenied

    today = timezone.now().date()
    all_periods = EvaluationPeriod.objects.all()

    # ── Filter options (real data only) ──
    school_years = list(
        all_periods.values_list("school_year", flat=True)
        .distinct()
        .order_by("-school_year")
    )

    latest_released = (
        all_periods.filter(end_date__lt=today, evaluations__teacher=teacher)
        .distinct()
        .order_by("-start_date")
        .first()
    )
    default_period = latest_released or all_periods.order_by("-start_date").first()

    selected = {
        "school_year": request.GET.get("school_year")
        or (default_period.school_year if default_period else ""),
        "semester": request.GET.get("semester")
        or (default_period.semester if default_period else ""),
        "term": request.GET.get("term")
        or (default_period.term if default_period else ""),
    }

    period = None
    state = "released"
    current = previous = previous_period = None

    if not all_periods.exists():
        state = "no_periods"
    else:
        period = all_periods.filter(**selected).first()
        if period is None:
            state = "not_found"
        elif period.status == "draft":
            state = "not_open"
        elif period.status == "active":
            state = "in_progress"      # results are NOT released yet
        else:                          # closed = finalized/released
            current = _get_scores(teacher, period)
            state = "released" if current else "no_responses"

    rows = []
    overall_change = None
    chart = None

    if state == "released":
        previous_period = (
            all_periods.filter(
                start_date__lt=period.start_date,
                end_date__lt=today,
                evaluations__teacher=teacher,
            )
            .distinct()
            .order_by("-start_date")
            .first()
        )
        if previous_period:
            previous = _get_scores(teacher, previous_period)

        for field, label in CATEGORIES:
            score = current["categories"][field]
            row = {
                "label": label,
                "score": score,
                "percent": round(score / 5 * 100),
                "level": _score_level(score),
            }
            if previous:
                prev = previous["categories"][field]
                diff = round(score - prev, 2)
                row.update(previous=prev, diff_text=f"{diff:+.2f}", trend=_trend(diff))
            rows.append(row)

        if previous:
            diff = round(current["overall"] - previous["overall"], 2)
            overall_change = {"text": f"{diff:+.2f}", "trend": _trend(diff)}

        chart = {
            "labels": [label for _, label in CATEGORIES],
            "current": [r["score"] for r in rows],
            "current_label": _period_label(period),
            "previous": [r["previous"] for r in rows] if previous else None,
            "previous_label": _period_label(previous_period) if previous else None,
        }

    context = {
        "active_tab": "results",
        "state": state,
        "period": period,
        "period_label": _period_label(period) if period else "",
        "school_years": school_years,
        "semester_choices": EvaluationPeriod.SEMESTER_CHOICES,
        "term_choices": EvaluationPeriod.TERM_CHOICES,
        "selected": selected,
        "has_released": latest_released is not None,
        "current": current,
        "previous": previous,
        "previous_period": previous_period,
        "previous_label": _period_label(previous_period) if previous_period else "",
        "overall_change": overall_change,
        "rows": rows,
        "results_chart": chart,
    }
    return render(request, "users/instructor_evaluation_results.html", context)
