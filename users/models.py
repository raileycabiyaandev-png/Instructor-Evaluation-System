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
    # Teachers belong to a department (Academic Management > Departments)
    department = models.ForeignKey(
        'academics.Department',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users',
    )

    must_change_password = models.BooleanField(default=True)

    program = models.CharField(
    max_length=10,
    choices=PROGRAM_CHOICES,
    blank=True
)
    # Students belong to a section such as "BSIT 2A" (Academic Management > Sections)
    section = models.ForeignKey(
        'academics.Section',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='students',
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"