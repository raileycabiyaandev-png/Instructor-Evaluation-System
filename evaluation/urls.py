from django.urls import path
from . import views

urlpatterns = [

    # Create new evaluation period
    path('evaluation/manage_evaluations/', views.manage_evaluations, name='manage_evaluations'),

    # Activate / Deactivate
    path('evaluation/toggle/<int:pk>/', views.toggle_active, name='toggle_active'),
    path('evaluation/update/<int:pk>/', views.update_evaluation, name='update_evaluation'),
    path('evaluation/delete/<int:pk>/', views.delete_evaluation, name='delete_evaluation'),
    path('evaluate/<int:teacher_id>/<int:pk>/', views.evaluate_teacher_page, name='evaluate_teacher_page'),
    path('evaluate/evaluation_archive/', views.evaluation_archive, name='evaluation_archive'),
    path('submit/<int:teacher_id>/<int:pk>/', views.submit_evaluation, name='submit_evaluation'),
    path('evaluation/archive/<int:pk>/', views.evaluation_term_detail, name='evaluation_term_detail'),
    path('qr/delete/<int:qr_id>/', views.delete_qr, name='delete_qr'),
    path('evaluation-closed/', views.evaluation_closed_page, name='evaluation_closed'),
    path('scan/', views.scan_qr_page, name='scan_qr_page'),
    path('qr/', views.qr_redirect_handler, name='qr_redirect_handler'),
    path('evaluation/results/',   views.faculty_results,   name='faculty_results'),
    path('evaluation/history/',   views.faculty_history,   name='faculty_history'),
    path('qr_list/', views.qr_list, name='qr_list'),
]