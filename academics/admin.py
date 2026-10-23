from django.contrib import admin
from .models import Department, Subject, Section, TeachingAssignment

admin.site.register(Department)
admin.site.register(Subject)
admin.site.register(Section)
admin.site.register(TeachingAssignment)
