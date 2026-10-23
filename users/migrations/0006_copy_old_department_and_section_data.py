# STEP B (data only)
#
# - Creates the two starting departments: Information Technology (IT)
#   and General Education (GENED), if they do not exist yet.
# - Instructors: old text 'it' / 'gened'  ->  the matching Department.
# - Students: old free-text section (example "BSIT-3B")  ->  a real Section
#   (only when the student has a program; the program decides the department).
#
# Nothing is deleted here. The old columns stay until migration 0007.

from django.db import migrations

LEGACY_DEPARTMENT_MAP = {
    'it': ('IT', 'Information Technology'),
    'gened': ('GENED', 'General Education'),
}

# Programs that belong to the IT department. Other programs have no
# department yet, so their old section text is left for the admin to redo.
IT_PROGRAMS = ('it',)


def forwards(apps, schema_editor):
    User = apps.get_model('users', 'User')
    Department = apps.get_model('academics', 'Department')
    Section = apps.get_model('academics', 'Section')

    departments = {}
    for legacy_value, (code, name) in LEGACY_DEPARTMENT_MAP.items():
        departments[legacy_value], _ = Department.objects.get_or_create(
            code=code, defaults={'name': name},
        )

    # Instructors / any user that had a department text
    for legacy_value, department in departments.items():
        User.objects.filter(legacy_department=legacy_value).update(department=department)

    # Students that had a free-text section
    skipped = []
    for student in User.objects.filter(role='student').exclude(legacy_section=''):
        if student.program not in IT_PROGRAMS:
            skipped.append(student.pk)
            continue

        section, _ = Section.objects.get_or_create(
            name=student.legacy_section.strip().upper(),
            defaults={'program': student.program, 'department': departments['it']},
        )
        student.section = section
        student.save(update_fields=['section'])

    if skipped:
        print(
            f"\n  [users 0006] {len(skipped)} student(s) had an old section text but "
            f"no IT program, so they were NOT given a section: ids {skipped}. "
            f"Assign them in Academic Management > Sections."
        )


def backwards(apps, schema_editor):
    User = apps.get_model('users', 'User')

    for legacy_value, (code, _name) in LEGACY_DEPARTMENT_MAP.items():
        User.objects.filter(department__code=code).update(legacy_department=legacy_value)

    for student in User.objects.filter(section__isnull=False).select_related('section'):
        student.legacy_section = student.section.name
        student.save(update_fields=['legacy_section'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_department_section_links'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
