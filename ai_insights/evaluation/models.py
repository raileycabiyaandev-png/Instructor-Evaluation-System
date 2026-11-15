# evaluation/models.py
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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

    # Which questionnaire (version) students answer in this period. It is filled in automatically
    # with the newest active version, and it can NOT be changed after the first evaluation is saved,
    # so old results always keep the questions they were really answered with.
    questionnaire = models.ForeignKey(
        'Questionnaire', null=True, blank=True, on_delete=models.PROTECT, related_name='periods',
    )

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

    def clean(self):
        super().clean()
        if self.pk and self.questionnaire_id:
            old = EvaluationPeriod.objects.filter(pk=self.pk).values_list('questionnaire_id', flat=True).first()
            if old and old != self.questionnaire_id and self.evaluations.exists():
                raise ValidationError("The questionnaire cannot be changed after students have submitted evaluations.")

    def save(self, *args, **kwargs):
        if not self.questionnaire_id:
            self.questionnaire = Questionnaire.current()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.school_year} - {self.semester} - {self.term}"


# =========================
# QUESTIONNAIRE (versioned)
# =========================
class Questionnaire(models.Model):
    """One version of the evaluation form. Example: v1 = 5 dimensions x 3 statements."""

    version = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(
        default=True, help_text="New evaluation periods use the newest active questionnaire.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']

    @classmethod
    def current(cls):
        """The questionnaire new periods get (newest active one), or None if none exists."""
        return cls.objects.filter(is_active=True).order_by('-version').first()

    @property
    def is_locked(self):
        """True once any student answered it. A locked questionnaire must not be edited: make a new version."""
        return EvaluationAnswer.objects.filter(question__category__questionnaire=self).exists()

    def questions(self):
        """All statements in screen order (category order, then statement order)."""
        return (EvaluationQuestion.objects.filter(category__questionnaire=self, is_active=True)
                .select_related('category').order_by('category__order', 'order', 'pk'))

    def __str__(self):
        return f"v{self.version} - {self.name}"


class EvaluationCategory(models.Model):
    """One dimension (for example 'Classroom Management') of one questionnaire version."""

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name='categories')
    key = models.SlugField(max_length=40, help_text="Fixed code name used by reports and AI. Do not rename.")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    weight = models.DecimalField(
        max_digits=4, decimal_places=2, default=1,
        validators=[MinValueValidator(0)],
        help_text="Share of this dimension in the overall score. All 1 = every dimension counts equally.")

    class Meta:
        ordering = ['questionnaire', 'order']
        unique_together = ('questionnaire', 'key')
        verbose_name_plural = 'evaluation categories'

    def __str__(self):
        return f"{self.name} (v{self.questionnaire.version})"


class EvaluationQuestion(models.Model):
    """One rating statement under a dimension."""

    category = models.ForeignKey(EvaluationCategory, on_delete=models.CASCADE, related_name='questions')
    text = models.CharField(max_length=300)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category__order', 'order', 'pk']

    def __str__(self):
        return self.text


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

    # The 1-5 ratings are NOT columns here any more: each statement answered is one EvaluationAnswer row
    # (15 per evaluation). See EvaluationAnswer below and evaluation/scoring.py for the averages.

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

        if not self.period.questionnaire_id:
            raise ValidationError("This evaluation period has no questionnaire.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def category_scores(self):
        """{category key: average of this student's 3 ratings} - see evaluation/scoring.py."""
        from . import scoring
        return scoring.evaluation_categories(self)

    def average_score(self):
        """This one evaluation's overall score (weighted mean of its 5 dimension averages)."""
        from . import scoring
        return scoring.evaluation_overall(self)

    def __str__(self):
        return f"{self.student} → {self.teacher} ({self.period})"


class EvaluationAnswer(models.Model):
    """One student's 1-5 rating of ONE statement. Anonymous in every report: only the Evaluation row links to the student."""

    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(EvaluationQuestion, on_delete=models.PROTECT, related_name='answers')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])

    class Meta:
        unique_together = ('evaluation', 'question')          # one rating per statement per evaluation
        constraints = [
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name='answer_rating_1_to_5'),
        ]

    def __str__(self):
        return f"{self.question_id}: {self.rating}"

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