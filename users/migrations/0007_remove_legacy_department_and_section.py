# STEP C: remove the old text columns (their data was copied in 0006).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_copy_old_department_and_section_data'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='legacy_department',
        ),
        migrations.RemoveField(
            model_name='user',
            name='legacy_section',
        ),
    ]
