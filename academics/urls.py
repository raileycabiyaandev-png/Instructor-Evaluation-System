from django.urls import path
from . import views

urlpatterns = [
    # Departments
    path('departments/', views.department_list, name='department_list'),
    path('departments/<int:pk>/edit/', views.department_update, name='department_update'),
    path('departments/<int:pk>/toggle/', views.department_toggle, name='department_toggle'),
    path('departments/<int:pk>/delete/', views.department_delete, name='department_delete'),

    # Subjects
    path('subjects/', views.subject_list, name='subject_list'),
    path('subjects/<int:pk>/edit/', views.subject_update, name='subject_update'),
    path('subjects/<int:pk>/toggle/', views.subject_toggle, name='subject_toggle'),
    path('subjects/<int:pk>/delete/', views.subject_delete, name='subject_delete'),

    # Sections (and the students inside them)
    path('sections/', views.section_list, name='section_list'),
    path('sections/<int:pk>/edit/', views.section_update, name='section_update'),
    path('sections/<int:pk>/toggle/', views.section_toggle, name='section_toggle'),
    path('sections/<int:pk>/delete/', views.section_delete, name='section_delete'),
    path('sections/<int:pk>/students/', views.section_students, name='section_students'),

    # Teaching assignments
    path('assignments/', views.assignment_list, name='assignment_list'),
    path('assignments/<int:pk>/edit/', views.assignment_update, name='assignment_update'),
    path('assignments/<int:pk>/delete/', views.assignment_delete, name='assignment_delete'),
]
