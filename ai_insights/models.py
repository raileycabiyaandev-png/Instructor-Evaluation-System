# ai_insights/models.py
#
# Only the AI RESULTS are stored here. Student ratings stay in
# evaluation.Evaluation. These tables never change any rating.

from django.conf import settings
from django.db import models
from django.utils import timezone

from evaluation.models import Evaluation, EvaluationPeriod


class ForecastResult(models.Model):
    """A saved forecast of ONE instructor's overall score for the NEXT period."""

    STATUS_OK = 'ok'
    STATUS_BASELINE = 'baseline'            # only 1 usable period: a starting point, not a forecast
    STATUS_INSUFFICIENT = 'insufficient'
    STATUS_UNRELIABLE = 'unreliable'        # the model's own test error is too big
    STATUS_ERROR = 'error'
    STATUS_CHOICES = (
        (STATUS_OK, 'Forecast made'),
        (STATUS_BASELINE, 'Baseline only'),
        (STATUS_INSUFFICIENT, 'Not enough data'),
        (STATUS_UNRELIABLE, 'Not reliable enough'),
        (STATUS_ERROR, 'Error'),
    )

    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='ai_forecasts', limit_choices_to={'role': 'instructor'},
    )
    # The period this analysis is based on (the newest finished period used).
    # The forecast is for the period AFTER it. Every status keeps it, so there is one row per
    # instructor per period.
    based_on_period = models.ForeignKey(
        EvaluationPeriod, on_delete=models.CASCADE,
        null=True, blank=True, related_name='ai_forecasts',
    )
    target_label = models.CharField(max_length=120, blank=True)
    # The period being predicted: {"term": "midterm", "semester": "1st", "school_year": "2025-2026"}
    target = models.JSONField(default=dict, blank=True)
    # What data was found and what was left out: {"periods_found": 5, "periods_used": 4,
    #   "skipped": [{"label": ..., "responses": 2, "reason": ...}], "total_responses": 62}
    data_quality = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    message = models.TextField(blank=True)

    predicted_overall = models.FloatField(null=True, blank=True)
    range_low = models.FloatField(null=True, blank=True)
    range_high = models.FloatField(null=True, blank=True)
    # {"teaching_effectiveness": {"value": 4.1, "low": 3.9, "high": 4.3, "mae": 0.1}, ...}
    predicted_categories = models.JSONField(default=dict, blank=True)

    # The REAL scores that were used: [{"label":..., "overall":..., "responses":...}, ...]
    history = models.JSONField(default=list, blank=True)
    periods_used = models.PositiveSmallIntegerField(default=0)

    model_name = models.CharField(max_length=60, blank=True)      # e.g. "trend"
    validation_mae = models.FloatField(null=True, blank=True)     # error of chosen model
    baseline_mae = models.FloatField(null=True, blank=True)       # error of "same as last period"
    validation_steps = models.PositiveSmallIntegerField(default=0)
    reliability = models.CharField(max_length=20, blank=True)     # "low" / "fair"
    library_version = models.CharField(max_length=60, blank=True)
    # Version of OUR forecasting method (not the library). Changes when the method changes.
    model_version = models.CharField(max_length=30, blank=True)
    # Walk-forward test results: for each past period, what the model WOULD have predicted
    # using only the periods before it, next to the REAL score:
    # [{"label": ..., "actual": 4.1, "predicted": 4.0}, ...]
    backtest = models.JSONField(default=list, blank=True)

    input_hash = models.CharField(max_length=64, blank=True)      # same data = skip recompute
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-generated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['instructor', 'based_on_period'],
                name='ai_one_forecast_per_instructor_period',
            ),
        ]

    def __str__(self):
        return f"Forecast {self.instructor} ({self.status})"


class CommentSentiment(models.Model):
    """Sentiment of ONE comment. The comment text is NOT copied here."""

    LABEL_CHOICES = (
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    )

    evaluation = models.OneToOneField(
        Evaluation, on_delete=models.CASCADE, related_name='ai_sentiment',
    )
    label = models.CharField(max_length=10, choices=LABEL_CHOICES)
    confidence = models.FloatField()
    model_name = models.CharField(max_length=120)
    analyzed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.label} ({self.confidence:.2f})"


class FeedbackSummary(models.Model):
    """Privacy-protected summary of the comments of ONE instructor in ONE period."""

    SOURCE_RULE = 'rule'
    SOURCE_OLLAMA = 'ollama'
    SOURCE_CHOICES = (
        (SOURCE_RULE, 'Rule-based text'),
        (SOURCE_OLLAMA, 'AI-written text (Ollama)'),
    )

    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='ai_feedback_summaries', limit_choices_to={'role': 'instructor'},
    )
    period = models.ForeignKey(
        EvaluationPeriod, on_delete=models.CASCADE, related_name='ai_feedback_summaries',
    )

    n_responses = models.PositiveIntegerField(default=0)
    n_comments = models.PositiveIntegerField(default=0)     # students who wrote something
    n_analyzed = models.PositiveIntegerField(default=0)     # usable comments
    n_skipped = models.PositiveIntegerField(default=0)      # empty / invalid comments
    positive = models.PositiveIntegerField(default=0)
    neutral = models.PositiveIntegerField(default=0)
    negative = models.PositiveIntegerField(default=0)

    # When True nothing is shown to the teacher (too few students / comments, or model missing)
    hidden = models.BooleanField(default=True)
    hidden_reason = models.CharField(max_length=200, blank=True)

    # [{"key": "punctuality", "label": "Punctuality", "mentions": 3, "positive": 0, "negative": 2}]
    themes = models.JSONField(default=list, blank=True)
    # Specific topics: [{"key": "late_start", "label": "...", "category": "punctuality", "tone": "problem", "mentions": 3, ...}]
    subthemes = models.JSONField(default=list, blank=True)
    # What students liked / want improved (short sentences, no quotes). Written by Ollama or by rules.
    positive_points = models.JSONField(default=list, blank=True)
    negative_points = models.JSONField(default=list, blank=True)

    summary_text = models.TextField(blank=True)
    summary_source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_RULE)
    llm_model = models.CharField(max_length=120, blank=True)
    sentiment_model = models.CharField(max_length=120, blank=True)

    input_hash = models.CharField(max_length=64, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-generated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['instructor', 'period'], name='ai_one_summary_per_instructor_period',
            ),
        ]

    def __str__(self):
        return f"Feedback summary {self.instructor} / {self.period}"


class Suggestion(models.Model):
    """One improvement suggestion. 'rule' = fixed rule, 'ollama' = AI-written text."""

    SOURCE_CHOICES = FeedbackSummary.SOURCE_CHOICES
    PRIORITY_CHOICES = (
        ('high', 'Needs attention'),
        ('medium', 'Can improve'),
        ('low', 'Small idea'),
        ('strength', 'Strength'),
    )

    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='ai_suggestions', limit_choices_to={'role': 'instructor'},
    )
    period = models.ForeignKey(
        EvaluationPeriod, on_delete=models.CASCADE, related_name='ai_suggestions',
    )
    category = models.CharField(max_length=40)          # a category field name, or "overall"
    category_label = models.CharField(max_length=60)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES)
    text = models.TextField()
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    # The real numbers behind the suggestion (shown to the teacher so it is transparent)
    evidence = models.JSONField(default=dict, blank=True)
    llm_model = models.CharField(max_length=120, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['source', 'id']

    def __str__(self):
        return f"{self.category_label}: {self.priority}"


class AnalysisRun(models.Model):
    """
    One OFFICIAL analysis made by the administrator for one evaluation period.
    Instructors can see AI results only after at least one run has finished.
    `version` counts the runs of the same period (1, 2, 3 ...), so a regeneration never
    hides what was done before.
    """

    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_COMPLETED_ERRORS = 'completed_errors'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_RUNNING, 'Running'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_COMPLETED_ERRORS, 'Completed with some errors'),
        (STATUS_FAILED, 'Failed'),
    )
    RELEASED = (STATUS_COMPLETED, STATUS_COMPLETED_ERRORS)

    TRIGGER_CHOICES = (
        ('manual', 'Administrator pressed the button'),
        ('command', 'Terminal command'),
        ('auto', 'Automatic (scheduled command)'),
    )

    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name='ai_runs')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    trigger = models.CharField(max_length=10, choices=TRIGGER_CHOICES, default='manual')
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='ai_runs_started',
    )
    regenerate = models.BooleanField(default=False)
    use_llm = models.BooleanField(default=False)

    engine_version = models.CharField(max_length=30, blank=True)     # forecasting method version
    data_fingerprint = models.CharField(max_length=64, blank=True)   # same data + same engine = up to date

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    total_instructors = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)
    # [{"instructor_id": 4, "message": "..."}]  (no student data)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(fields=['period', 'version'], name='ai_run_version_per_period'),
            # The database itself allows only ONE running analysis at a time.
            models.UniqueConstraint(
                fields=['status'], condition=models.Q(status='running'),
                name='ai_only_one_running_run',
            ),
        ]

    def __str__(self):
        return f"Analysis v{self.version} of {self.period} ({self.status})"

    @property
    def is_running(self):
        return self.status == self.STATUS_RUNNING

    @property
    def is_released(self):
        return self.status in self.RELEASED

    @property
    def percent(self):
        if not self.total_instructors:
            return 100 if not self.is_running else 0
        return min(100, round((self.processed + self.failed) / self.total_instructors * 100))


# ─────────────────────────────────────────────
# ALERTS (admin) — "signals to check", never a judgement of a teacher
# ─────────────────────────────────────────────
class AlertSettings(models.Model):
    """The thresholds the administrator can change. There is only ONE row (id=1)."""

    low_score = models.FloatField(default=3.5)            # latest overall score below this
    score_drop = models.FloatField(default=0.3)           # fell by this much since the last period
    falling_periods = models.PositiveSmallIntegerField(default=2)   # this many falls in a row
    forecast_low = models.FloatField(default=3.5)         # forecast below this
    forecast_drop = models.FloatField(default=0.3)        # forecast is this much below the last real score
    negative_share = models.PositiveSmallIntegerField(default=40)   # % of comments that are negative
    category_low = models.FloatField(default=3.0)         # any category below this
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    @classmethod
    def get(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row

    def __str__(self):
        return "Alert thresholds"


class Alert(models.Model):
    KIND_CHOICES = (
        ('low_score', 'Low score'),
        ('score_drop', 'Score dropped'),
        ('falling', 'Falling for several periods'),
        ('forecast_low', 'Low forecast'),
        ('forecast_drop', 'Forecast drop'),
        ('negative_feedback', 'Many negative comments'),
        ('category_low', 'Low category'),
    )
    SEVERITY_CHOICES = (('high', 'High'), ('medium', 'Medium'), ('low', 'Low'))
    STATUS_CHOICES = (('new', 'New'), ('reviewed', 'Reviewed'), ('resolved', 'Resolved'))

    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='ai_alerts', limit_choices_to={'role': 'instructor'})
    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name='ai_alerts')
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    severity = models.CharField(max_length=8, choices=SEVERITY_CHOICES, default='medium')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='new')
    title = models.CharField(max_length=160)
    message = models.TextField()
    evidence = models.JSONField(default=dict, blank=True)    # the real numbers behind the alert
    note = models.TextField(blank=True)                      # the administrator's own note
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    run = models.ForeignKey(AnalysisRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='alerts')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One alert of a kind per instructor per period: running the analysis again never makes copies.
        constraints = [models.UniqueConstraint(fields=['instructor', 'period', 'kind'], name='ai_alert_once')]
        indexes = [models.Index(fields=['status', 'severity'])]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_kind_display()} · {self.instructor_id} · {self.period_id}"


# ─────────────────────────────────────────────
# NOTICES for instructors (about their OWN results only)
# ─────────────────────────────────────────────
class InstructorNotice(models.Model):
    KIND_CHOICES = (('ready', 'Analysis ready'), ('up', 'Score went up'), ('down', 'Score went down'))

    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='ai_notices', limit_choices_to={'role': 'instructor'})
    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name='ai_notices')
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    title = models.CharField(max_length=160)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['instructor', 'period', 'kind'], name='ai_notice_once')]
        ordering = ['-created_at', '-id']


# ─────────────────────────────────────────────
# REPORT HISTORY (who exported what; the file itself is not stored)
# ─────────────────────────────────────────────
class ReportLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    kind = models.CharField(max_length=20)       # instructor / department / summary / my_report
    fmt = models.CharField(max_length=4)         # pdf / xlsx
    title = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']


# ─────────────────────────────────────────────
# School-level AI text for ONE evaluation period (admin only)
# ─────────────────────────────────────────────
class SchoolInsight(models.Model):
    """
    The school-wide summary, strengths, weaknesses and suggestions of one period.
    Built from COUNTS only (scores, topic counts). No comment text and no instructor names go in.
    The text is written by Ollama when it is on and passes the safety checks, otherwise by plain rules.
    """
    SOURCE_CHOICES = (('ollama', 'AI (Ollama)'), ('rule', 'Rule-based'))

    period = models.OneToOneField(EvaluationPeriod, on_delete=models.CASCADE, related_name='ai_school_insight')
    run = models.ForeignKey(AnalysisRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    summary_text = models.TextField(blank=True)
    summary_source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='rule')
    strengths = models.JSONField(default=list, blank=True)       # [{"text", "source"}]
    weaknesses = models.JSONField(default=list, blank=True)      # [{"text", "source"}]
    suggestions = models.JSONField(default=list, blank=True)     # [{"text", "source", "label"}]
    facts = models.JSONField(default=dict, blank=True)           # the numbers the text was made from
    llm_model = models.CharField(max_length=120, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"School insight {self.period}"