# users/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser

# models.py
class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('student', 'Student'),
        ('instructor', 'Instructor'),
    )

    DEPARTMENT_CHOICES = (
        ('it', 'IT Department'),
        ('gened', 'General Education Department'),
    )

    PROGRAM_CHOICES = (
        ('it', 'BSIT'),
        ('cs', 'BSCS'),
        ('hm', 'BSHM'),
        ('ad', 'BSAD'),
        ('ac', 'BSA'),
    )

    is_active = models.BooleanField(default=True)
    
    email = models.EmailField(unique=True)
    middle_name = models.CharField(max_length=50, blank=True)

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES, blank=True, null=True)

    must_change_password = models.BooleanField(default=True)

    program = models.CharField(
    max_length=10,
    choices=PROGRAM_CHOICES,
    blank=True
)
    section = models.CharField(max_length=50, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"