# ai_insights/conf.py
#
# ALL the AI settings in ONE place.
# These can be changed in project/settings.py by adding the setting with the
# name shown in each function below (AI_SENTIMENT_MODEL, AI_SENTIMENT_NOTE,
# AI_OLLAMA_URL, AI_OLLAMA_MODEL, AI_OLLAMA_TIMEOUT, AI_OLLAMA_ENABLED).
# If you add nothing, the defaults below are used.
# The number rules at the top (MIN_RESPONSES ...) are changed here in this file.

from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


# --- Privacy / data rules (same idea as MIN_RESPONSES in users/views.py) ---
MIN_RESPONSES = 3          # fewer student responses than this -> nothing about comments is shown
MIN_COMMENTS = 3           # fewer usable comments than this -> no sentiment summary is shown
MIN_THEME_MENTIONS = 2     # a theme must come from at least this many comments

# --- Forecast rules ---
MIN_PERIODS = 4            # finished periods needed (3 to learn from + at least 1 to test on)
MIN_TRAIN = 3              # smallest number of past periods a test model may learn from
LOW_RELIABILITY_BELOW = 6  # fewer periods than this -> forecast is marked "low reliability"
STEADY_BAND = 0.05         # predicted change smaller than this is called "steady"

# A forecast is shown only if its OWN test error (average miss in the walk-forward test)
# is not bigger than this many points (on the 1-5 scale). Above it, no prediction is shown.
# Can be changed in settings.py with AI_MAX_VALIDATION_MAE.
DEFAULT_MAX_VALIDATION_MAE = 0.6

# Version of the forecasting method. Change it when the method changes, so old saved
# forecasts are recomputed and every page can say which version made them.
FORECAST_MODEL_VERSION = 'forecast-v3'

# A run that says "running" for longer than this is treated as stopped (server restart, crash).
STALE_RUN_MINUTES = 30


def max_validation_mae():
    return _get('AI_MAX_VALIDATION_MAE', DEFAULT_MAX_VALIDATION_MAE)


def run_in_background():
    # True: the "Generate AI Analysis" button starts the work in a background thread and the page
    # shows progress. False: it runs inside the request (used by the tests and the command).
    return _get('AI_RUN_IN_BACKGROUND', True)


def sentiment_model_name():
    # Multilingual (many languages) model with 3 classes: positive / neutral / negative.
    return _get('AI_SENTIMENT_MODEL', 'lxyuan/distilbert-base-multilingual-cased-sentiments-student')


def sentiment_note():
    # Shown to teachers under the comment summary. If you change the model, change this note too.
    return _get(
        'AI_SENTIMENT_NOTE',
        "This model was trained on 12 languages (English, Arabic, German, Spanish, French, Japanese, "
        "Chinese, Indonesian, Hindi, Italian, Malay and Portuguese). Filipino is not one of them, "
        "so Tagalog or Taglish comments may be classified less accurately.",
    )


def ollama_url():
    return _get('AI_OLLAMA_URL', 'http://localhost:11434')


def ollama_model():
    return _get('AI_OLLAMA_MODEL', 'llama3.2')


def ollama_timeout():
    return _get('AI_OLLAMA_TIMEOUT', 180)   # seconds (a local model on a CPU can be slow)


def ollama_enabled():
    return _get('AI_OLLAMA_ENABLED', True)