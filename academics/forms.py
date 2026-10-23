# academics/forms.py
from django import forms

from users.models import User
from .models import Department, Subject, Section, TeachingAssignment
from .utils import (
    semester_options,
    current_semester,
    semester_label,
    encode_semester,
    decode_semester,
)

INPUT = {'class': 'form-control'}


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['code', 'name', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. IT', 'autocomplete': 'off'}),
            'name': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. Information Technology', 'autocomplete': 'off'}),
        }

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()

    def clean_name(self):
        return self.cleaned_data['name'].strip()


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ['code', 'name', 'department', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. IT101', 'autocomplete': 'off'}),
            'name': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. Web Development', 'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only active departments can be picked (but keep the current one when editing)
        departments = Department.objects.filter(is_active=True)
        if self.instance.pk:
            departments = departments | Department.objects.filter(pk=self.instance.department_id)
        self.fields['department'].queryset = departments.distinct()
        self.fields['department'].empty_label = '— Select department —'

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['name', 'program', 'department', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. BSIT 2A', 'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        departments = Department.objects.filter(is_active=True)
        if self.instance.pk:
            departments = departments | Department.objects.filter(pk=self.instance.department_id)
        self.fields['department'].queryset = departments.distinct()
        self.fields['department'].empty_label = '— Select department —'

    def clean_name(self):
        return self.cleaned_data['name'].strip().upper()


class TeacherChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        dept = user.department.code if user.department else 'No department'
        return f"{user.get_full_name()} ({dept})"


class TeachingAssignmentForm(forms.ModelForm):
    teacher = TeacherChoiceField(queryset=User.objects.none(), empty_label='— Select teacher —')

    # One dropdown that holds BOTH school year and semester.
    semester_choice = forms.ChoiceField(label='School year & semester')

    class Meta:
        model = TeachingAssignment
        fields = ['teacher', 'subject', 'section']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Teachers: active instructors only (any department - GenEd may teach IT students)
        teachers = User.objects.filter(role='instructor', is_active=True)
        subjects = Subject.objects.filter(is_active=True)
        sections = Section.objects.filter(is_active=True)

        # When editing, keep the saved values selectable even if they were deactivated later
        if self.instance.pk:
            teachers = teachers | User.objects.filter(pk=self.instance.teacher_id)
            subjects = subjects | Subject.objects.filter(pk=self.instance.subject_id)
            sections = sections | Section.objects.filter(pk=self.instance.section_id)

        self.fields['teacher'].queryset = (
            teachers.select_related('department').distinct().order_by('last_name', 'first_name')
        )
        self.fields['subject'].queryset = subjects.distinct()
        self.fields['section'].queryset = sections.distinct()
        self.fields['subject'].empty_label = '— Select subject —'
        self.fields['section'].empty_label = '— Select section —'

        # Semester dropdown comes from the Evaluation Periods
        options = semester_options()
        self.fields['semester_choice'].choices = [
            (encode_semester(sy, sem), semester_label(sy, sem)) for sy, sem in options
        ]

        if self.instance.pk:
            self.fields['semester_choice'].initial = encode_semester(
                self.instance.school_year, self.instance.semester
            )
        else:
            current = current_semester()
            if current:
                self.fields['semester_choice'].initial = encode_semester(*current)

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()

        value = cleaned.get('semester_choice')
        subject = cleaned.get('subject')
        section = cleaned.get('section')

        if value:
            school_year, semester = decode_semester(value)
            self.instance.school_year = school_year
            self.instance.semester = semester

            # One teacher per subject + section + semester
            if subject and section:
                clash = TeachingAssignment.objects.filter(
                    subject=subject,
                    section=section,
                    school_year=school_year,
                    semester=semester,
                ).exclude(pk=self.instance.pk).select_related('teacher').first()

                if clash:
                    raise forms.ValidationError(
                        f"{subject.code} in {section.name} already has a teacher for this "
                        f"semester: {clash.teacher.get_full_name()}. "
                        f"Edit that assignment instead."
                    )

        return cleaned
