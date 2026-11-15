# STEP 2 of 3 - DATA.
#   * Creates questionnaire v1: 5 dimensions x 3 statements (copied here on purpose: a migration must not
#     change later if the code file changes).
#   * Links every evaluation period to v1.
#   * FRESH START (approved by the project owner, system not deployed yet): deletes the old evaluations,
#     because they have 5 old-style numbers and cannot be turned into 15 real answers. We never invent answers.
#     The AI results made from them are removed first, by ai_insights migration 0008.
# Periods, users, sections, teaching assignments and QR codes are NOT touched.

from django.db import migrations

DIMENSIONS = [
    ('teaching_competence', 'Teaching Competence',
     'Subject knowledge, lesson organization and effective teaching methods.',
     ['Shows strong knowledge of the subject.',
      'Organizes lessons so they are easy to follow.',
      'Uses teaching methods that help me learn.']),
    ('communication_clarity', 'Communication and Clarity',
     'Clear explanations, understandable instructions and effective communication.',
     ['Explains topics clearly.',
      'Gives instructions for activities and requirements that are easy to understand.',
      'Communicates respectfully and listens to students.']),
    ('classroom_management', 'Classroom Management',
     'Organized learning environment, class time management and fair treatment.',
     ['Keeps the learning environment organized.',
      'Uses class time well.',
      'Treats all students fairly.']),
    ('student_engagement_support', 'Student Engagement and Support',
     'Encourages participation, answers questions and supports learning.',
     ['Encourages students to take part in class.',
      'Answers questions helpfully.',
      'Supports my learning with feedback and help.']),
    ('professionalism_reliability', 'Professionalism and Reliability',
     'Punctuality, preparedness and consistent fulfillment of teaching responsibilities.',
     ['Starts and ends class on time.',
      'Comes prepared for class.',
      'Consistently fulfills teaching responsibilities, such as following the schedule and returning work on time.']),
]


def create_v1_and_fresh_start(apps, schema_editor):
    Questionnaire = apps.get_model('evaluation', 'Questionnaire')
    Category = apps.get_model('evaluation', 'EvaluationCategory')
    Question = apps.get_model('evaluation', 'EvaluationQuestion')
    Period = apps.get_model('evaluation', 'EvaluationPeriod')
    Evaluation = apps.get_model('evaluation', 'Evaluation')

    if not Questionnaire.objects.filter(version=1).exists():
        questionnaire = Questionnaire.objects.create(version=1, name='Instructor Evaluation', is_active=True)
        for order, (key, name, description, statements) in enumerate(DIMENSIONS, start=1):
            category = Category.objects.create(
                questionnaire=questionnaire, key=key, name=name, description=description, order=order, weight=1)
            for number, text in enumerate(statements, start=1):
                Question.objects.create(category=category, text=text, order=number)
    questionnaire = Questionnaire.objects.get(version=1)

    Evaluation.objects.all().delete()          # fresh start (see the note at the top of this file)
    Period.objects.filter(questionnaire__isnull=True).update(questionnaire=questionnaire)


class Migration(migrations.Migration):

    dependencies = [
        ('evaluation', '0008_questionnaire'),
        ('ai_insights', '0008_clear_results_for_new_questionnaire'),   # old AI results go first
    ]

    operations = [
        # Going backwards keeps the data (the deleted evaluations cannot be brought back).
        migrations.RunPython(create_v1_and_fresh_start, migrations.RunPython.noop),
    ]
