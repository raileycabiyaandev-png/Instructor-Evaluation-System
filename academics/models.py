# academics/models.py
from django.core.exceptions import ValidationError
from django.db import models

from evaluation.models import EvaluationPeriod
from users.models import User


class Department(models.Model):
    code = models.CharField(max_length=10, unique=True)  
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Subject(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)         
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='subjects',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class Section(models.Model):
    name = models.CharField(max_length=50, unique=True)      # e.g. BSIT 2A
    program = models.CharField(max_length=10, choices=User.PROGRAM_CHOICES)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='sections',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class TeachingAssignment(models.Model):
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,   # same behaviour as Evaluation.teacher
        related_name='teaching_assignments',
        limit_choices_to={'role': 'instructor'},
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='assignments',
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        related_name='assignments',
    )
    school_year = models.CharField(max_length=20)            # e.g. 2026-2027
    semester = models.CharField(max_length=20, choices=EvaluationPeriod.SEMESTER_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-school_year', 'semester', 'section__name', 'subject__code']
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'section', 'school_year', 'semester'],
                name='unique_subject_section_per_semester',
            ),
        ]

    def clean(self):
        if self.teacher_id and self.teacher.role != 'instructor':
            raise ValidationError("Only instructor accounts can be assigned to teach.")

    def __str__(self):
        return (
            f"{self.teacher} - {self.subject.code} - {self.section} "
            f"({self.school_year} {self.get_semester_display()})"
        )
