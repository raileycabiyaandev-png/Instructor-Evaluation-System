from django.contrib import admin
from .models import Evaluation, EvaluationPeriod, QRCode

admin.site.register(EvaluationPeriod)
admin.site.register(QRCode)

# Questionnaire (read-mostly): the admin can look at the versions, statements and weights.
from .models import EvaluationCategory, EvaluationQuestion, Questionnaire


class CategoryInline(admin.TabularInline):
    model = EvaluationCategory
    extra = 0
    fields = ('order', 'key', 'name', 'weight')
    readonly_fields = ('key',)


@admin.register(Questionnaire)
class QuestionnaireAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_active', 'created_at')
    inlines = [CategoryInline]

    def has_delete_permission(self, request, obj=None):
        return False if obj is None or obj.is_locked else super().has_delete_permission(request, obj)


@admin.register(EvaluationQuestion)
class EvaluationQuestionAdmin(admin.ModelAdmin):
    list_display = ('text', 'category', 'order', 'is_active')
    list_filter = ('category__questionnaire', 'category')

    def has_change_permission(self, request, obj=None):
        # a statement that students already answered must not be reworded: create a new questionnaire version instead
        if obj is not None and obj.answers.exists():
            return False
        return super().has_change_permission(request, obj)
