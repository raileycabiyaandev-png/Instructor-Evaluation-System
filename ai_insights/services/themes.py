# ai_insights/services/themes.py
#
# Common feedback THEMES, found with a plain keyword list (no AI, fully visible).
# Each theme is one of the five evaluation categories. Words are English and
# common Filipino (Tagalog) words, because students may write in either.
#
# Only COUNTS leave this file. No comment text and no student information.

import re

from .. import conf
from .data import CATEGORY_LABELS

KEYWORDS = {
    'teaching_effectiveness': [
        'teach', 'explain', 'lesson', 'lecture', 'understand', 'learn', 'topic', 'example',
        'clear', 'knowledge', 'quiz', 'exam', 'grading', 'turo', 'magturo', 'naiintindihan',
        'hindi maintindihan', 'leksyon', 'aralin', 'paliwanag',
    ],
    'communication_skills': [
        'communicat', 'speak', 'voice', 'listen', 'question', 'answer', 'respond', 'reply',
        'language', 'approachable', 'friendly', 'rude', 'sagot', 'tanong', 'magsalita',
        'boses', 'mabait', 'masungit',
    ],
    'classroom_management': [
        'discipline', 'noise', 'noisy', 'control', 'rules', 'order', 'organized', 'organised',
        'fair', 'strict', 'attendance', 'seatwork', 'maingay', 'disiplina', 'ayos', 'gulo',
    ],
    'punctuality': [
        'late', 'on time', 'punctual', 'early', 'absent', 'cancel', 'schedule', 'wait',
        'dismiss', 'huli', 'late dumating', 'absent si', 'oras', 'maaga', 'nahuhuli',
    ],
    'student_engagement': [
        'engage', 'interest', 'boring', 'fun', 'activity', 'activities', 'participat', 'motivat',
        'interactive', 'group', 'game', 'sleepy', 'nakakaantok', 'masaya', 'nakakabagot', 'gawain',
    ],
}

_PATTERNS = {
    key: re.compile(r'(?<![a-z])(?:' + '|'.join(re.escape(w) for w in words) + ')', re.IGNORECASE)
    for key, words in KEYWORDS.items()
}


def find_themes(items):
    """
    items: list of (cleaned_comment, sentiment_label).
    Returns themes that at least MIN_THEME_MENTIONS different comments support,
    biggest first. A comment can support more than one theme.
    """
    counts = {key: {'mentions': 0, 'positive': 0, 'negative': 0} for key in KEYWORDS}

    for text, label in items:
        for key, pattern in _PATTERNS.items():
            if pattern.search(text):
                counts[key]['mentions'] += 1
                if label == 'positive':
                    counts[key]['positive'] += 1
                elif label == 'negative':
                    counts[key]['negative'] += 1

    themes = [
        {'key': key, 'label': CATEGORY_LABELS[key], **c}
        for key, c in counts.items()
        if c['mentions'] >= conf.MIN_THEME_MENTIONS
    ]
    return sorted(themes, key=lambda t: (-t['mentions'], t['key']))


# ─────────────────────────────────────────────
# SUB-TOPICS: specific things students write about (inside each of the five categories)
# ─────────────────────────────────────────────
# tone 'problem' = students describe something to fix; tone 'praise' = something they like.
# A topic is listed only when at least MIN_THEME_MENTIONS different comments mention it.
# `unless` removes false matches ("never late" must not count as "late").
# `action` is the concrete step suggested for a problem topic; `strength` is the sentence for a praise topic.
# Only COUNTS leave this file. No comment text and no student information.

_NOT = r"(?:never|not|hindi|di|wasn'?t|isn'?t|no)\s+(?:ever\s+|naman\s+)?"

SUBTOPICS = {
    'teaching_effectiveness': [
        {'key': 'unclear_explanation', 'label': 'Lessons are hard to understand', 'tone': 'problem',
         'words': ['hard to understand', 'difficult to understand', 'not clear', 'unclear', 'confus',
                   'hindi maintindihan', 'hindi ko maintindihan', 'di maintindihan', 'mahirap intindihin',
                   "can't follow", 'cannot follow', 'hard to follow'],
         'action': "Slow down on new ideas, show one worked example for each, and ask one short check question before moving on."},
        {'key': 'fast_pace', 'label': 'Lessons move too fast', 'tone': 'problem',
         'words': ['too fast', 'so fast', 'very fast', 'rushed', 'napakabilis', 'ang bilis', 'minadali', 'mabilis magturo'],
         'action': "Split each lesson into smaller parts and pause after every part for questions."},
        {'key': 'few_examples', 'label': 'Not enough examples', 'tone': 'problem',
         'words': ['no example', 'more example', 'lack of example', 'few example', 'need example',
                   'walang example', 'kulang sa example', 'kulang ang example'],
         'action': "Add two or three real examples to every new topic, and let students try one on their own."},
        {'key': 'exams_workload', 'label': 'Exams, grading or workload', 'tone': 'problem',
         'words': ['unfair grad', 'grading is', 'hard exam', 'difficult exam', 'exam is too', 'exams are too',
                   'too many quiz', 'too many assign', 'too much homework', 'too many activities',
                   'sobrang daming', 'hirap ng exam', 'mahirap ang exam'],
         'action': "Share the grading rules and exam coverage early, and spread deadlines so work does not pile up."},
        {'key': 'explains_well', 'label': 'Explains lessons well', 'tone': 'praise',
         'words': ['well explained', 'explains well', 'explain well', 'explains clearly', 'explain clearly',
                   'easy to understand', 'clear explanation', 'madaling intindihin', 'naiintindihan',
                   'galing magturo', 'magaling magturo', 'maganda magturo', 'good teacher', 'great teacher'],
         'strength': "Students praise how clearly you explain the lessons."},
        {'key': 'knowledgeable', 'label': 'Knows the subject well', 'tone': 'praise',
         'words': ['knowledgeable', 'knows the subject', 'knows his', 'knows her', 'expert', 'marunong', 'magaling'],
         'strength': "Students notice how well you know your subject."},
    ],
    'communication_skills': [
        {'key': 'unapproachable', 'label': 'Hard to approach', 'tone': 'problem',
         'words': ['rude', 'masungit', 'snob', 'intimidat', 'scary', 'afraid to ask', 'takot magtanong',
                   'shout', 'sigaw', 'harsh', 'nakakatakot'],
         'action': "Answer questions calmly, thank students for asking, and avoid raising your voice in front of the class."},
        {'key': 'no_reply', 'label': 'Questions not answered', 'tone': 'problem',
         'words': ['ignore', 'not answer', "doesn't answer", 'does not answer', "didn't answer", 'no reply',
                   "didn't respond", 'not responsive', 'slow reply', 'hindi sumasagot', 'walang sagot', 'di sumasagot'],
         'action': "Set a fixed time for questions in class and reply to messages within one school day."},
        {'key': 'voice', 'label': 'Voice is hard to hear', 'tone': 'problem',
         'words': ["can't hear", 'cannot hear', 'hard to hear', 'soft voice', 'low voice', 'mumbl',
                   'mahina ang boses', 'di marinig', 'hindi marinig', 'mahina boses'],
         'action': "Speak louder, face the class when you talk, and use a microphone in large rooms."},
        {'key': 'approachable', 'label': 'Friendly and easy to talk to', 'tone': 'praise',
         'words': ['approachable', 'friendly', 'kind', 'mabait', 'easy to talk', 'welcomes question',
                   'listens', 'nakikinig', 'nice teacher', 'respectful'],
         'strength': "Students find you friendly and easy to approach."},
    ],
    'classroom_management': [
        {'key': 'noisy', 'label': 'Noisy or out of control class', 'tone': 'problem',
         'words': ['noisy', 'noise', 'maingay', 'ingay', 'out of control', 'chaos', 'magulo ang klase',
                   'walang disiplina', 'no discipline'],
         'action': "Agree on class rules again, use a clear signal for quiet time, and act early when noise starts."},
        {'key': 'unfair', 'label': 'Rules feel unfair', 'tone': 'problem',
         'words': ['unfair', 'favorit', 'not fair', 'hindi patas', 'pabor', 'inconsistent', 'too strict', 'masyadong istrikto'],
         'action': "Apply the same rules to every student, and explain the reason behind each rule."},
        {'key': 'disorganized', 'label': 'Class is disorganized', 'tone': 'problem',
         'words': ['disorganized', 'disorganised', 'unorganized', 'unorganised', 'not organized', 'not organised',
                   'no plan', 'walang plano', 'wasted time', 'sayang oras', 'magulo ang'],
         'action': "Write the plan of the day on the board and give each activity a time limit."},
        {'key': 'well_managed', 'label': 'Well-organised, fair class', 'tone': 'praise',
         'words': ['well organized', 'well organised', 'organized', 'organised', 'disciplined', 'maayos', 'orderly', 'patas', 'fair'],
         'strength': "Students see your class as organised and fair."},
    ],
    'punctuality': [
        {'key': 'late_start', 'label': 'Class starts or ends late', 'tone': 'problem',
         'words': ['late', 'lagi late', 'laging late', 'nahuhuli', 'huli dumating', 'starts late', 'dismiss late'],
         'unless': _NOT + r'late',
         'action': "Start and end on the scheduled minute, and prepare materials before the class begins."},
        {'key': 'absences', 'label': 'Missed or cancelled classes', 'tone': 'problem',
         'words': ['absent', 'cancel', 'no class', 'walang pasok', 'walang klase', 'hindi pumasok', 'di pumasok',
                   'missed class', 'skip'],
         'unless': _NOT + r'(?:absent|cancel)',
         'action': "Tell students early when a class is moved or cancelled, and give a make-up plan."},
        {'key': 'on_time', 'label': 'Comes on time', 'tone': 'praise',
         'words': ['on time', 'punctual', 'maaga', 'never late', 'not late', 'hindi late', 'always early',
                   'sakto sa oras', 'laging maaga'],
         'strength': "Students notice that you come on time."},
    ],
    'student_engagement': [
        {'key': 'boring', 'label': 'Lessons feel boring', 'tone': 'problem',
         'words': ['boring', 'nakakabagot', 'nakakaantok', 'sleepy', 'monotone', 'dull', 'walang gana'],
         'action': "Change the activity about every 15 minutes, and start with a short question or problem."},
        {'key': 'lecture_only', 'label': 'Mostly lecture, few activities', 'tone': 'problem',
         'words': ['puro lecture', 'lecture only', 'only lectur', 'no activit', 'lack of activit', 'walang activity',
                   'walang gawain', 'not interactive', 'no interaction', 'hindi interactive'],
         'action': "Add one short activity to every class, such as pair talk, a quick quiz, or a group task."},
        {'key': 'fun', 'label': 'Fun and interactive lessons', 'tone': 'praise',
         'words': ['fun', 'interactive', 'masaya', 'enjoyable', 'engaging', 'interesting', 'group work',
                   'games', 'nakakatuwa', 'activities'],
         'strength': "Students enjoy your activities and lessons."},
    ],
}

_COMPILED = {}
for _category, _topics in SUBTOPICS.items():
    for _t in _topics:
        _COMPILED[_t['key']] = (
            re.compile(r'(?<![a-z])(?:' + '|'.join(re.escape(w) for w in _t['words']) + ')', re.IGNORECASE),
            re.compile(_t['unless'], re.IGNORECASE) if _t.get('unless') else None,
        )

TOPIC_BY_KEY = {t['key']: {**t, 'category': c} for c, topics in SUBTOPICS.items() for t in topics}


def find_subtopics(items):
    """
    items: list of (cleaned_comment, sentiment_label).
    Returns the specific topics that at least MIN_THEME_MENTIONS different comments mention.
    A praise topic is NOT counted for a comment that also has a problem topic in the same category
    (so "not clear" is never counted as "clear").
    """
    counts = {}
    for text, label in items:
        for category, topics in SUBTOPICS.items():
            hits = []
            for t in topics:
                pattern, unless = _COMPILED[t['key']]
                if pattern.search(text) and not (unless and unless.search(text)):
                    hits.append(t)
            if any(t['tone'] == 'problem' for t in hits):
                hits = [t for t in hits if t['tone'] == 'problem']
            for t in hits:
                c = counts.setdefault(t['key'], {'mentions': 0, 'positive': 0, 'negative': 0})
                c['mentions'] += 1
                if label == 'positive':
                    c['positive'] += 1
                elif label == 'negative':
                    c['negative'] += 1

    found = []
    for key, c in counts.items():
        if c['mentions'] < conf.MIN_THEME_MENTIONS:
            continue
        t = TOPIC_BY_KEY[key]
        found.append({'key': key, 'label': t['label'], 'category': t['category'],
                      'category_label': CATEGORY_LABELS[t['category']], 'tone': t['tone'], **c})
    return sorted(found, key=lambda s: (-s['mentions'], s['key']))