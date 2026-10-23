from django import forms
from .models import EvaluationPeriod
from django.utils import timezone


class EvaluationPeriodForm(forms.ModelForm):

    class Meta:
        model = EvaluationPeriod

        fields = [
            'school_year',
            'semester',
            'term',
            'start_date',
            'end_date',
            'is_active'
        ]

        widgets = {
            'school_year': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 2026-2027',
                'autocomplete': 'off'
            }),

            'semester': forms.Select(attrs={
                'class': 'form-control'
            }),

            'term': forms.Select(attrs={
                'class': 'form-control'
            }),

            'start_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control'
            }),

            'end_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control'
            }),

            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }

    def clean(self):
        cleaned_data = super().clean()

        start = cleaned_data.get("start_date")
        end = cleaned_data.get("end_date")

        if start and end:
            if start > end:
                raise forms.ValidationError(
                    "End date must be after start date."
                )

        return cleaned_data