import uuid
from django import forms
from django.contrib.auth.forms import AuthenticationForm
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
# ADMIN CREATE STUDENT
# =========================
class StudentCreateForm(forms.ModelForm):
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
            'department',
            'password'
        ]

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


class StudentFirstLoginForm(forms.ModelForm):
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


class InstructorFirstLoginForm(forms.ModelForm):
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
            'department',
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

            'department': forms.TextInput(attrs={
                'placeholder': 'Enter department'
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
    
class StudentUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'program',
        ]

class InstructorUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'middle_name', 'last_name', 'email', 'department']