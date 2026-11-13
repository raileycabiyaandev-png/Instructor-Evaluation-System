# ai_insights/services/sentiment.py
#
# Sentiment of student comments with a pretrained Hugging Face model.
#
# Safety rules:
#   * The model is loaded ONLY when needed, and only ONCE (cached).
#   * We only use a model whose labels are really positive / neutral / negative.
#     A model with other labels (for example 1-5 stars) is refused.
#   * Empty or junk comments are skipped, never guessed.
#   * Any loading or running problem becomes SentimentUnavailable, so the
#     rest of the system keeps working.

import re

from .. import conf

REQUIRED_LABELS = {'positive', 'neutral', 'negative'}
MAX_CHARS = 1000


class SentimentUnavailable(Exception):
    """The model could not be loaded or used. The message is safe to show."""


_cache = {}


def clean_comment(text):
    """Return the cleaned comment, or None when it is empty or junk."""
    if not isinstance(text, str):
        return None
    text = re.sub(r'[\x00-\x1f\x7f]+', ' ', text)         # control characters
    text = re.sub(r'\s+', ' ', text).strip()
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return None
    if len(text) > 6 and len(set(c.lower() for c in letters)) <= 2:   # "aaaaaaa", "hahahaha"
        return None
    return text[:MAX_CHARS]


def get_classifier(model_name=None):
    """Load the Hugging Face pipeline once. Tests replace this function."""
    model_name = model_name or conf.sentiment_model_name()
    if model_name in _cache:
        return _cache[model_name]

    try:
        from transformers import pipeline          # imported late: heavy library
        classifier = pipeline(
            'text-classification', model=model_name, top_k=None,
            truncation=True, max_length=256,
        )
        labels = {str(v).lower() for v in classifier.model.config.id2label.values()}
    except Exception as exc:                        # missing package, no internet, bad name ...
        raise SentimentUnavailable(
            f"The sentiment model '{model_name}' could not be loaded ({type(exc).__name__})."
        ) from exc

    if not REQUIRED_LABELS.issubset(labels):
        raise SentimentUnavailable(
            f"The model '{model_name}' does not give positive / neutral / negative "
            f"(it gives: {', '.join(sorted(labels))}), so it is not used."
        )

    _cache[model_name] = classifier
    return classifier


def classify(texts, model_name=None):
    """
    texts: list of already-cleaned comments.
    Returns a list of (label, confidence) in the same order.
    """
    if not texts:
        return []
    classifier = get_classifier(model_name)

    try:
        raw = classifier(list(texts))
    except Exception as exc:
        raise SentimentUnavailable(f"The sentiment model failed while running ({type(exc).__name__}).") from exc

    results = []
    for item in raw:
        scores = item if isinstance(item, list) else [item]
        best = max(scores, key=lambda s: s['score'])
        label = str(best['label']).lower()
        if label not in REQUIRED_LABELS:
            raise SentimentUnavailable(f"The model returned an unexpected label '{label}'.")
        results.append((label, round(float(best['score']), 4)))
    return results