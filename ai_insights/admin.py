from django.contrib import admin

from .models import AnalysisRun, CommentSentiment, FeedbackSummary, ForecastResult, Suggestion


@admin.register(ForecastResult)
class ForecastResultAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'status', 'target_label', 'predicted_overall', 'model_name', 'generated_at')
    list_filter = ('status', 'model_name')
    readonly_fields = [f.name for f in ForecastResult._meta.fields]


@admin.register(FeedbackSummary)
class FeedbackSummaryAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'period', 'n_analyzed', 'hidden', 'summary_source', 'generated_at')
    readonly_fields = [f.name for f in FeedbackSummary._meta.fields]


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'period', 'category_label', 'priority', 'source')
    readonly_fields = [f.name for f in Suggestion._meta.fields]


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ('period', 'version', 'status', 'trigger', 'started_by', 'started_at', 'finished_at')
    list_filter = ('status', 'trigger')
    readonly_fields = [f.name for f in AnalysisRun._meta.fields]


# CommentSentiment is not registered on purpose: it points to single evaluations,
# so it is kept out of the admin screens to protect student anonymity.