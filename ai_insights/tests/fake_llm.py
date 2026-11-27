# A stand-in for Ollama in tests: it answers in the JSON shape the real code asks for.
import json

from ai_insights.services import ollama


def fake_generate(prompt, system=None, as_json=False, max_tokens=300):
    if system == ollama.SUMMARY_SYSTEM:
        return json.dumps({
            'summary': ('Students describe the class in a mostly friendly way. They liked how you explain lessons. '
                        'They ask for a few small changes, mainly about starting on time.'),
            'positive_points': ['Friendly manner in class'],
            'negative_points': ['Class sometimes starts late'],
        })
    if system == ollama.RECOMMEND_SYSTEM:
        line = [l for l in prompt.splitlines() if l.startswith('Write advice for these keys')][0]
        keys = line.split(':', 1)[1].strip().rstrip('.').split(', ')
        return json.dumps({
            'overall': 'This period went reasonably well. Keep the habits that work and improve one thing at a time.',
            'categories': {k: f"Keep working on {k.replace('_', ' ')} with small steps. Check the result next period." for k in keys},
        })
    return "A calm note. Keep going, you are doing well."