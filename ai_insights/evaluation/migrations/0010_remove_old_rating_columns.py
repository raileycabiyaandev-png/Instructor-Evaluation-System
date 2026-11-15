# STEP 3 of 3: drop the 5 old rating columns from Evaluation. The ratings now live in EvaluationAnswer.
# (They are made nullable first only so that this step can be reversed on an EMPTY table.)

from django.db import migrations, models

OLD_COLUMNS = ['teaching_effectiveness', 'communication_skills', 'classroom_management',
               'punctuality', 'student_engagement']


class Migration(migrations.Migration):

    dependencies = [
        ('evaluation', '0009_questionnaire_v1_fresh_start'),
    ]

    operations = (
        [migrations.AlterField(model_name='evaluation', name=name, field=models.PositiveIntegerField(null=True))
         for name in OLD_COLUMNS]
        + [migrations.RemoveField(model_name='evaluation', name=name) for name in OLD_COLUMNS]
    )
