# evaluation/questionnaire_data.py

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
