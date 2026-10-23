from django.shortcuts import render
from django.db.models import Avg
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import EvaluationPeriod, Evaluation
from .forms import EvaluationPeriodForm
from django.http import HttpResponse
from users.models import User
from django.db.models import Avg
from .models import QRCode
from evaluation.models import EvaluationPeriod
import qrcode
from io import BytesIO
from django.core.files import File
from django.contrib import messages
from django.utils import timezone
from django.db.models import Avg, Count
from django.db.models import Q
from collections import defaultdict
import json
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator

def no_cache(view_func): # prevent caching 
    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response
    return wrapper

def get_active_period():
    return EvaluationPeriod.objects.filter(is_active=True).first()


def set_active_period(period):
    EvaluationPeriod.objects.update(is_active=False)
    period.is_active = True
    period.save()

def auto_close_expired_periods():
    today = timezone.now().date()

    EvaluationPeriod.objects.filter(
        end_date__lt=today,
        is_active=True
    ).update(is_active=False)


def admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != 'admin':
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper

RATING_FIELDS = [
    ('teaching_effectiveness', 'Teaching Effectiveness'),
    ('communication_skills',   'Communication Skills'),
    ('classroom_management',   'Classroom Management'),
    ('punctuality',            'Punctuality'),
    ('student_engagement',     'Student Engagement'),
]


@login_required
@no_cache
@admin_required
def manage_evaluations(request):

    # AUTO CLOSE EXPIRED PERIODS
    auto_close_expired_periods()

    today = timezone.now().date()

    # FORCE EXPIRED ACTIVE PERIODS TO CLOSE
    EvaluationPeriod.objects.filter(
        end_date__lte=today,
        is_active=True
    ).update(is_active=False)

    # SHOW ONLY:
    # - ACTIVE
    # - DRAFT
    # HIDE CLOSED
    periods = EvaluationPeriod.objects.filter(
        end_date__gt=today
    ).order_by('-start_date')

    if request.method == 'POST':

        form = EvaluationPeriodForm(request.POST)

        if form.is_valid():

            period = form.save()

            # ONLY ONE ACTIVE PERIOD
            if period.is_active:
                set_active_period(period)

            messages.success(
                request,
                "Evaluation period created successfully."
            )

            return redirect('manage_evaluations')

        messages.error(
            request,
            "Please correct the errors below."
        )

    else:
        form = EvaluationPeriodForm()

    return render(request, 'evaluation/manage_evaluations.html', {
        'form': form,
        'periods': periods,
    })


# Toggle on and Off
@login_required
@no_cache
@admin_required
def toggle_active(request, pk):

    period = get_object_or_404(EvaluationPeriod, pk=pk)

    if period.is_active:
        period.is_active = False
        period.save()
    else:
        set_active_period(period)

    return redirect('manage_evaluations')


def safe_rating(value):
    try:
        value = int(value)

        if 1 <= value <= 5:
            return value

    except (TypeError, ValueError):
        pass

    return None

@login_required
@no_cache
@admin_required
def update_evaluation(request, pk):

    period = get_object_or_404(EvaluationPeriod, pk=pk)

    if request.method == 'POST':
        form = EvaluationPeriodForm(request.POST, instance=period)

        if form.is_valid():
            updated = form.save()

            if updated.is_active:
                set_active_period(updated)

            messages.success(request, "Evaluation updated successfully.")
            return redirect('manage_evaluations')
    else:
        form = EvaluationPeriodForm(instance=period)

    return render(request, 'evaluation/update_evaluation.html', {
        'form': form
    })

@login_required
@no_cache
@admin_required
def delete_evaluation(request, pk):
    period = get_object_or_404(EvaluationPeriod, pk=pk)

    if request.method == 'POST':
        period.delete()
        messages.success(request, "Evaluation deleted successfully.")
        return redirect('manage_evaluations')

    return render(request, 'evaluation/confirm_delete.html', {
        'period': period
    })

@login_required
def submit_evaluation(request, teacher_id, pk):

    if request.method != 'POST':
        return HttpResponse("Invalid request", status=405)

    teacher = get_object_or_404(
        User,
        id=teacher_id,
        role='instructor'
    )

    period = get_object_or_404(
        EvaluationPeriod,
        id=pk
    )

    # prevent duplicate evaluation
    if Evaluation.objects.filter(
        student=request.user,
        teacher=teacher,
        period=period
    ).exists():
        messages.warning(request, "Already evaluated this instructor.")
        return redirect('student_dashboard')

    ratings = {
        field: safe_rating(request.POST.get(field))
        for field, _ in RATING_FIELDS
    }

    if None in ratings.values():
        messages.error(request, "Complete all ratings.")
        return redirect('evaluate_teacher_page', teacher_id=teacher.id, pk=period.id)

    Evaluation.objects.create(
        student=request.user,
        teacher=teacher,
        period=period,
        comment=request.POST.get('comment', '').strip(),
        **ratings
    )

    messages.success(request, "Evaluation submitted successfully.")
    return redirect('student_dashboard')

@login_required
@no_cache
def evaluate_teacher_page(request, teacher_id, pk):
    """
    Displays the star-rating evaluation form for a specific teacher
    and period.  Access is blocked if the period is closed or the
    student has already submitted.
    """
    period  = get_object_or_404(EvaluationPeriod, pk=pk)
    teacher = get_object_or_404(User, id=teacher_id, role='instructor')
 
    # 🚫 Period closed — hard redirect
    if not period.is_active:
        return redirect('evaluation_closed')
 
    already_evaluated = Evaluation.objects.filter(
        student=request.user,
        teacher=teacher,
        period=period,
    ).exists()
 
    return render(request, 'evaluation/evaluate_teacher.html', {
        'teacher':           teacher,
        'period':            period,
        'ratings':           RATING_FIELDS,
        'already_evaluated': already_evaluated,
    })


@login_required
@no_cache
@admin_required
def qr_list(request):

    # Generate QR
    if request.method == 'POST':

        instructor_id = request.POST.get('instructor')
    
        instructor = get_object_or_404(
            User,
            id=instructor_id,
            role='instructor'
        )

        # prevent duplicate QR per instructor
        existing_qr = QRCode.objects.filter(
            instructor=instructor
        ).first()

        if existing_qr:
            messages.warning(
                request,
                "This instructor already has a permanent QR code."
            )
            return redirect('qr_list')

        # permanent QR URL
        base_url = "http://127.0.0.1:8000"

        evaluation_url = (
            f"{base_url}/evaluation/qr/"
            f"?teacher={instructor.id}"
        )

        # generate QR image
        qr = qrcode.make(evaluation_url)

        buffer = BytesIO()
        qr.save(buffer, format='PNG')
        buffer.seek(0)

        # create QR record
        qr_code = QRCode.objects.create(
            instructor=instructor
        )

        file_name = f'qr_instructor_{instructor.id}.png'

        qr_code.qr_image.save(
            file_name,
            File(buffer),
            save=True
        )

        messages.success(
            request,
            "Permanent QR Code generated successfully."
        )

        return redirect('qr_list')

    
    # LIST QR CODES
    qr_codes = QRCode.objects.all().order_by('-updated_at')

    return render(request, 'evaluation/qr_list.html', {
        'qr_codes': qr_codes,
        'instructors': User.objects.filter(role='instructor'),
    })

@login_required
@no_cache
@admin_required
def delete_qr(request, qr_id):
    qr = get_object_or_404(QRCode, id=qr_id)

    if request.method == "POST":
        qr.delete()
        messages.success(request, "QR Code deleted successfully.")
        return redirect('qr_list')

    return redirect('qr_list')

@login_required
def evaluation_closed_page(request):
    return render(request, 'evaluation/evaluation_closed.html')

@login_required
def scan_qr_page(request):
    return render(request, 'evaluation/scan_qr.html')

@login_required
def qr_redirect_handler(request):

    teacher_id = request.GET.get('teacher')

    if not teacher_id:
        messages.error(request, "Invalid QR code.")
        return redirect('student_dashboard')

    teacher = get_object_or_404(
        User,
        id=teacher_id,
        role='instructor'
    )

    # =========================
    # GET ACTIVE PERIOD
    # =========================
    period = EvaluationPeriod.objects.filter(
        is_active=True
    ).first()

    # =========================
    # NO ACTIVE PERIOD
    # =========================
    if not period:
        return render(
            request,
            'evaluation/evaluation_closed.html'
        )

    # =========================
    # ALREADY EVALUATED
    # =========================
    already = Evaluation.objects.filter(
        student=request.user,
        teacher=teacher,
        period=period
    ).exists()

    if already:
        return render(
            request,
            'evaluation/already_submitted.html'
        )

    return redirect(
        'evaluate_teacher_page',
        teacher_id=teacher.id,
        pk=period.id
    )

@login_required
@never_cache
@admin_required
def evaluation_archive(request):

    auto_close_expired_periods()

    today = timezone.now().date()

    archived_periods = EvaluationPeriod.objects.filter(
        end_date__lte=today
    ).order_by(
        '-school_year',
        '-semester',
        '-term'
    )

    return render(
        request,
        "evaluation/evaluation_archive.html",
        {
            "archived_periods": archived_periods
        }
    )

@login_required
@never_cache
@admin_required
def evaluation_term_detail(request, pk):

    period = get_object_or_404(EvaluationPeriod, pk=pk)

    evaluations = Evaluation.objects.filter(period=period)

    total_submissions = evaluations.count()

    instructors = User.objects.filter(role='instructor')

    instructor_data = []

    for instructor in instructors:

        qs = evaluations.filter(teacher=instructor)

        if qs.exists():

            avg = qs.aggregate(
                teaching=Avg('teaching_effectiveness'),
                communication=Avg('communication_skills'),
                management=Avg('classroom_management'),
                punctuality=Avg('punctuality'),
                engagement=Avg('student_engagement'),
            )

            overall = (
                (avg['teaching'] or 0) +
                (avg['communication'] or 0) +
                (avg['management'] or 0) +
                (avg['punctuality'] or 0) +
                (avg['engagement'] or 0)
            ) / 5

            instructor_data.append({
                "instructor": instructor,
                "avg": avg,
                "count": qs.count(),
                "overall": round(overall, 2),
            })

    # =========================
    # RANKING (HIGHEST FIRST)
    # =========================
    instructor_data = sorted(
        instructor_data,
        key=lambda x: x["overall"],
        reverse=True
    )

    # =========================
    # BEST / WORST
    # =========================
    best_teacher = instructor_data[0] if instructor_data else None
    worst_teacher = instructor_data[-1] if instructor_data else None

    # =========================
    # CATEGORY AVERAGES (SYSTEM-WIDE)
    # =========================
    category_avg = evaluations.aggregate(
        teaching=Avg('teaching_effectiveness'),
        communication=Avg('communication_skills'),
        management=Avg('classroom_management'),
        punctuality=Avg('punctuality'),
        engagement=Avg('student_engagement'),
    )


    comments = evaluations.exclude(comment="").values_list("comment", flat=True)

    return render(request, "evaluation/evaluation_term_detail.html", {
        "period": period,
        "instructor_data": instructor_data,
        "total_submissions": total_submissions,
        "best_teacher": best_teacher,
        "worst_teacher": worst_teacher,
        "category_avg": category_avg,
        "comments": comments,
    })

def instructor_required(view_func):
    """Blocks non-instructors from reaching these views."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != 'instructor':
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper

def compute_averages(qs):
    """
    Given an Evaluation queryset, return:
      {
        'category_averages': {'Teaching Effectiveness': 4.25, ...},
        'overall_average': 4.10,
        'total_responses': 12,
      }
    """
    agg = qs.aggregate(
        teaching=Avg('teaching_effectiveness'),
        communication=Avg('communication_skills'),
        management=Avg('classroom_management'),
        punctuality=Avg('punctuality'),
        engagement=Avg('student_engagement'),
        total=Count('id'),
    )
 
    raw = {
        'Teaching Effectiveness': agg['teaching']  or 0,
        'Communication Skills':   agg['communication'] or 0,
        'Classroom Management':   agg['management'] or 0,
        'Punctuality':            agg['punctuality'] or 0,
        'Student Engagement':     agg['engagement'] or 0,
    }
 
    # Round each to 2 dp
    category_averages = {k: round(v, 2) for k, v in raw.items()}
 
    total = agg['total'] or 0
    overall = round(sum(raw.values()) / 5, 2) if total else 0
 
    return {
        'category_averages': category_averages,
        'overall_average':   overall,
        'total_responses':   total,
    }

@login_required
@instructor_required
def faculty_history(request):
    """
    Organises ALL closed periods the instructor has been
    evaluated in by:  School Year → Semester → Term
    Returns structured data for the accordion UI + a flat
    dict (history_detail_data) serialised to JSON so the
    template can show per-period detail without extra AJAX.
    """
    instructor = request.user
    today      = timezone.now().date()
 
    # Only closed periods where this instructor has at least one evaluation
    closed_periods = (
        EvaluationPeriod.objects
        .filter(
            end_date__lt=today,
            evaluations__teacher=instructor,
        )
        .distinct()
        .order_by('school_year', 'semester', 'term')
    )
 
    # ── Build nested structure ──────────────────────────────
    # {school_year: {semester: [period, ...]}}
    nested = defaultdict(lambda: defaultdict(list))
 
    for p in closed_periods:
        nested[p.school_year][p.get_semester_display()].append(p)
 
    history_by_year = []
    detail_data     = {}          # period.pk → stats + comments (for JS)
 
    for sy in sorted(nested.keys(), reverse=True):
        year_block = {'school_year': sy, 'semesters': []}
 
        for sem_label in sorted(nested[sy].keys()):
            sem_block = {'semester': sem_label, 'terms': []}
 
            for period in nested[sy][sem_label]:
                qs    = Evaluation.objects.filter(teacher=instructor, period=period)
                stats = compute_averages(qs)
 
                term_entry = {
                    'period_id':      period.pk,
                    'term':           period.get_term_display(),
                    'overall_avg':    stats['overall_average'],
                    'response_count': stats['total_responses'],
                }
                sem_block['terms'].append(term_entry)
 
                # Detail data for JS drawer
                comments_qs = (
                    qs.exclude(comment='')
                      .values_list('comment', 'created_at')
                      .order_by('-created_at')
                )
                detail_data[str(period.pk)] = {
                    'name':         str(period),
                    'period_label': f"{period.get_term_display()} — {period.school_year}",
                    'categories':   [
                        {'name': k, 'avg': v}
                        for k, v in stats['category_averages'].items()
                    ],
                    'comments': [
                        {
                            'text': c[0],
                            'date': c[1].strftime('%b %d, %Y') if c[1] else '',
                        }
                        for c in comments_qs
                    ],
                    'overall_avg':    stats['overall_average'],
                    'total_responses': stats['total_responses'],
                }
 
            year_block['semesters'].append(sem_block)
        history_by_year.append(year_block)
 
    return render(request, 'evaluation/faculty_dashboard.html', {
        'active_tab':          'history',
        'history_by_year':     history_by_year,
        'history_detail_data': json.dumps(detail_data),
    })

@login_required
@instructor_required
def faculty_results(request):
    """
    Full breakdown for the active period:
      • category scores
      • total evaluations
      • anonymous comments
    """
    instructor     = request.user
    current_period = EvaluationPeriod.objects.filter(is_active=True).first()
 
    if current_period:
        qs = Evaluation.objects.filter(
            teacher=instructor,
            period=current_period,
        )
    else:
        qs = Evaluation.objects.none()
 
    stats    = compute_averages(qs)
    comments = (
        qs.exclude(comment='')
          .values_list('comment', 'created_at')
          .order_by('-created_at')
    )
 
    return render(request, 'evaluation/faculty_dashboard.html', {
        'active_tab':        'results',
        'current_period':    current_period,
        'overall_average':   stats['overall_average'],
        'total_responses':   stats['total_responses'],
        'category_averages': stats['category_averages'],
        'comments':          comments,
    })