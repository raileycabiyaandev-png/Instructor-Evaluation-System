# Removes the AI results that were made from the OLD 5-category evaluations (fresh start, approved by the
# project owner). Their saved numbers use the old category names, so they cannot be shown with the new
# questionnaire. After the migration, an admin presses "Generate analysis" again when new evaluations exist.
#
# Kept: AlertSettings (the admin's thresholds) and ReportLog (download history).
# Deleted: everything the AI made. The comment sentiments go away with the old evaluations.

from django.db import migrations

DERIVED = ['CommentSentiment', 'ForecastResult', 'FeedbackSummary', 'Suggestion',
           'Alert', 'InstructorNotice', 'SchoolInsight', 'AnalysisRun']


def clear_old_ai_results(apps, schema_editor):
    for name in DERIVED:
        apps.get_model('ai_insights', name).objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ai_insights', '0007_alert_low_severity'),
        ('evaluation', '0008_questionnaire'),
    ]

    operations = [
        migrations.RunPython(clear_old_ai_results, migrations.RunPython.noop),
    ]
