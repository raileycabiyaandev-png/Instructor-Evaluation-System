# evaluation/questionnaire_data.py
#
# THE QUESTIONNAIRE IN ONE PLACE
#   * 5 dimensions (the "five evaluation categories" of the capstone) x 3 statements = 15 statements.
#   * The 1-5 Likert scale words.
#
# WHAT USES THIS FILE
#   * The migration that creates questionnaire v1 (so the 15 statements are in the database).
#   * evaluation/scoring.py and the AI files: they need the 5 category KEYS and LABELS at import time,
#     without asking the database. The five dimensions are fixed by the capstone requirements.
#   * Tests and demo-data scripts.
#
# The statement TEXT that students see comes from the database (EvaluationQuestion), so a future
# questionnaire version can reword statements without touching the code. If a future version ever
# changes the five dimensions themselves, change DIMENSIONS here and add a new version.

# (rating, label): shown from best to worst
LIKERT = [
    (5, 'Excellent'),
    (4, 'Very Good'),
    (3, 'Satisfactory'),
    (2, 'Needs Improvement'),
    (1, 'Poor'),
]
LIKERT_LABELS = dict(LIKERT)

# (key, name, what it covers, [3 statements])
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

# What the AI and report files import: [(key, label), ...] in screen order
CATEGORIES = [(key, name) for key, name, _, _ in DIMENSIONS]
CATEGORY_KEYS = [key for key, _ in CATEGORIES]
CATEGORY_LABELS = dict(CATEGORIES)

STATEMENTS_PER_DIMENSION = 3
TOTAL_STATEMENTS = len(DIMENSIONS) * STATEMENTS_PER_DIMENSION          # 15
