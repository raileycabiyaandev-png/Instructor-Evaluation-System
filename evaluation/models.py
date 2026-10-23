# evaluation/models.py
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from users.models import User
from django.utils import timezone

# evaluation.app (models.py)
class EvaluationPeriod(models.Model):

    SEMESTER_CHOICES = (
        ('1st', 'First Semester'),
        ('2nd', 'Second Semester'),
    )

    TERM_CHOICES = (
        ('prelims', 'Prelims'),
        ('midterm', 'Midterm'),
        ('prefinals', 'Pre-Finals'),
        ('finals', 'Finals'),
    )

    school_year = models.CharField(max_length=20)

    semester = models.CharField(
        max_length=20,
        choices=SEMESTER_CHOICES
    )

    term = models.CharField(
        max_length=20,
        choices=TERM_CHOICES
    )

    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False)

    # =========================
    # STATUS HELPERS (NEW)
    # =========================
    @property
    def is_closed(self):
        return self.end_date < timezone.now().date()

    @property
    def status(self):

        today = timezone.now().date()

        if self.end_date < today:
            return "closed"

        if self.is_active:
            return "active"

        return "draft"

    def __str__(self):
        return f"{self.school_year} - {self.semester} - {self.term}"
    

class Evaluation(models.Model):
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='given_evaluations'
    )

    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_evaluations'
    )

    # ✅ NEW FIELD
    period = models.ForeignKey(
        EvaluationPeriod,
        on_delete=models.CASCADE,
        related_name='evaluations'
    )

    teaching_effectiveness = models.PositiveIntegerField()
    communication_skills = models.PositiveIntegerField()
    classroom_management = models.PositiveIntegerField()
    punctuality = models.PositiveIntegerField()
    student_engagement = models.PositiveIntegerField()

    comment = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # ✅ Prevent duplicate PER PERIOD
        unique_together = ('student', 'teacher', 'period')

    def clean(self):
        if self.student.role != 'student':
            raise ValidationError("Only students can submit evaluations.")

        if self.teacher.role != 'instructor':
            raise ValidationError("You can only evaluate instructors.")

        if not self.period.is_active:
            raise ValidationError("Evaluation period is closed.")

        fields = [
            self.teaching_effectiveness,
            self.communication_skills,
            self.classroom_management,
            self.punctuality,
            self.student_engagement
        ]

        for value in fields:
            if value < 1 or value > 5:
                raise ValidationError("Ratings must be between 1 and 5.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def average_score(self):
        total = (
            self.teaching_effectiveness +
            self.communication_skills +
            self.classroom_management +
            self.punctuality +
            self.student_engagement
        )
        return round(total / 5, 2)

    def __str__(self):
        return f"{self.student} → {self.teacher} ({self.period})"

class QRCode(models.Model):
    instructor = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'instructor'}
    )

    qr_image = models.ImageField(upload_to='qr_codes/')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"QR - {self.instructor.get_full_name()}"