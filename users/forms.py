import uuid
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from academics.models import Department, Section
from .models import User

# =========================
# LOGIN FORM
# =========================
class LoginForm(forms.Form):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "username",
            }
        )
    )

    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
            }
        )
    )

# =========================
# SMALL SHARED HELPERS
# =========================
def active_departments(current=None):
    """Active departments (plus the one already saved, when editing)."""
    queryset = Department.objects.filter(is_active=True)
    if current:
        queryset = queryset | Department.objects.filter(pk=current)
    return queryset.distinct()


def active_sections(current=None):
    """Active sections (plus the one already saved, when editing)."""
    queryset = Section.objects.filter(is_active=True)
    if current:
        queryset = queryset | Section.objects.filter(pk=current)
    return queryset.distinct()


class StudentSectionMixin:
    """Adds the Section dropdown to student forms and checks it matches the program."""

    def setup_section_field(self):
        self.fields['section'].queryset = active_sections(self.instance.section_id)
        self.fields['section'].empty_label = '— No section yet —'
        self.fields['section'].required = False

    def clean(self):
        cleaned_data = super().clean()
        section = cleaned_data.get('section')
        program = cleaned_data.get('program')

        if section and program != section.program:
            raise forms.ValidationError(
                f"{section.name} is a {section.get_program_display()} section. "
                f"Set the student's program to {section.get_program_display()} first."
            )

        return cleaned_data


class NewPasswordMixin:
    """Rules for the NEW password chosen on first login."""

    def clean_password(self):
        password = self.cleaned_data['password']

        # The saved (temporary) password must not be reused
        if self.instance.pk and self.instance.check_password(password):
            raise forms.ValidationError(
                "Your new password must be different from the temporary password."
            )

        # Django's password rules (length, too common, only numbers, ...)
        validate_password(password, self.instance)
        return password


# =========================
# ADMIN CREATE STUDENT
# =========================
class StudentCreateForm(StudentSectionMixin, forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = [
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'program',
            'section',
            'password'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_section_field()

    def clean_email(self):
        email = self.cleaned_data['email']

        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Email already exists")

        return email

    def save(self, commit=True):
        user = super().save(commit=False)

        # ✅ REQUIRED: UNIQUE USERNAME
        user.username = f"student_{uuid.uuid4().hex[:8]}"

        user.role = 'student'
        user.set_password(self.cleaned_data['password'])
        user.must_change_password = True

        if commit:
            user.save()

        return user

# =========================
# ADMIN CREATE INSTRUCTOR
# =========================
class InstructorCreateForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ['first_name', 'middle_name', 'last_name', 'email', 'department', 'password']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = active_departments()
        self.fields['department'].empty_label = '— Select department —'
        self.fields['department'].required = True   # every teacher needs a department

    def save(self, commit=True):
        user = super().save(commit=False)

        user.role = 'instructor'
        user.username = self.cleaned_data['email']  # FIXED (IMPORTANT)

        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)

        user.must_change_password = True

        if commit:
            user.save()

        return user


class StudentFirstLoginForm(NewPasswordMixin, forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter new password'
        })
    )

    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password'
        })
    )

    class Meta:
        model = User
        fields = [
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'password',
            'confirm_password'
        ]

        widgets = {
            'first_name': forms.TextInput(attrs={
                'placeholder': 'Enter first name'
            }),

            'middle_name': forms.TextInput(attrs={
                'placeholder': 'Enter middle name'
            }),

            'last_name': forms.TextInput(attrs={
                'placeholder': 'Enter last name'
            }),

            'email': forms.EmailInput(attrs={
                'placeholder': 'Enter email'
            }),
        }

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password and confirm_password:
            if password != confirm_password:
                raise forms.ValidationError(
                    "Passwords do not match."
                )

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)

        user.set_password(
            self.cleaned_data['password']
        )

        user.must_change_password = False

        if commit:
            user.save()

        return user


class InstructorFirstLoginForm(NewPasswordMixin, forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter new password'
        })
    )

    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password'
        })
    )

    class Meta:
        model = User
        fields = [
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'password',
            'confirm_password'
        ]

        widgets = {
            'first_name': forms.TextInput(attrs={
                'placeholder': 'Enter first name'
            }),

            'middle_name': forms.TextInput(attrs={
                'placeholder': 'Enter middle name'
            }),

            'last_name': forms.TextInput(attrs={
                'placeholder': 'Enter last name'
            }),

            'email': forms.EmailInput(attrs={
                'placeholder': 'Enter email'
            }),
        }

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password and confirm_password:
            if password != confirm_password:
                raise forms.ValidationError(
                    "Passwords do not match."
                )

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)

        user.set_password(
            self.cleaned_data['password']
        )

        user.must_change_password = False

        if commit:
            user.save()

        return user
    
class StudentUpdateForm(StudentSectionMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'program',
            'section',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_section_field()


class InstructorUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'middle_name', 'last_name', 'email', 'department']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = active_departments(self.instance.department_id)
        self.fields['department'].empty_label = '— Select department —'
        self.fields['department'].required = True


# =========================
# STUDENT EDITS OWN PROFILE
# (name + email only. Program and section are set by the Admin.)
# =========================
class StudentProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'middle_name', 'last_name', 'email']

    def clean_first_name(self):
        value = self.cleaned_data['first_name'].strip()
        if not value:
            raise forms.ValidationError("First name is required.")
        return value

    def clean_last_name(self):
        value = self.cleaned_data['last_name'].strip()
        if not value:
            raise forms.ValidationError("Last name is required.")
        return value

    def clean_email(self):
        email = self.cleaned_data['email'].strip()

        taken = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists()
        if taken:
            raise forms.ValidationError("That email address is already used by another account.")

        return email