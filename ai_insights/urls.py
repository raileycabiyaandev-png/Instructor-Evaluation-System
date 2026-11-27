from django.urls import path

from . import views

urlpatterns = [
    path('admin/analytics/', views.admin_analytics, name='ai_admin_analytics'),
    path('admin/forecasting/', views.admin_forecasting, name='ai_admin_forecasting'),
    path('admin/feedback/', views.admin_feedback, name='ai_admin_feedback'),
    path('admin/recommendations/', views.admin_recommendations, name='ai_admin_recommendations'),
    path('admin/trends/', views.admin_trends, name='ai_admin_trends'),
    path('admin/alerts/', views.admin_alerts, name='ai_admin_alerts'),
    path('admin/alerts/settings/', views.admin_alert_settings, name='ai_admin_alert_settings'),
    path('admin/alerts/<int:pk>/update/', views.admin_alert_update, name='ai_admin_alert_update'),
    path('admin/instructor/<int:pk>/', views.admin_instructor, name='ai_admin_instructor'),
    path('admin/reports/', views.admin_reports, name='ai_admin_reports'),
    path('admin/reports/download/', views.admin_report_download, name='ai_admin_report_download'),
    path('my-report/', views.my_report, name='ai_my_report'),
    path('notices/read/', views.notices_read, name='ai_notices_read'),
    path('admin/generate/', views.admin_generate, name='ai_admin_generate'),
    path('admin/run-status/', views.admin_run_status, name='ai_admin_run_status'),
]