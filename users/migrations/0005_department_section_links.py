# STEP A (structure only, no data is lost here)
#
# 1. Fixes the old mismatch: the `status` field from migration 0004 is not
#    in the User model (the project uses `is_active`), so it is removed.
# 2. Renames the OLD text fields to legacy_department / legacy_section so
#    their data is kept safely until it is copied (migration 0006).
# 3. Adds the NEW department / section fields that point to real tables.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0001_initial'),
        ('users', '0004_user_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='status',
        ),
        migrations.AlterField(
            model_name='user',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.RenameField(
            model_name='user',
            old_name='department',
            new_name='legacy_department',
        ),
        migrations.RenameField(
            model_name='user',
            old_name='section',
            new_name='legacy_section',
        ),
        migrations.AddField(
            model_name='user',
            name='department',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='users',
                to='academics.department',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='section',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='students',
                to='academics.section',
            ),
        ),
    ]
