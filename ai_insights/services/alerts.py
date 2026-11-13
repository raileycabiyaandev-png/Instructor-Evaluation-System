# ai_insights/services/alerts.py
#
# PERFORMANCE ALERTS. An alert is a "signal to check", made by a visible rule from REAL numbers.
# It never says a teacher is bad. The thresholds belong to the administrator (AlertSettings).
#
# Rules (every number below can be changed on the Alerts page):
#   low_score          latest overall score is below  low_score            (default 3.5)       -> high
#   score_drop         score fell by at least         score_drop            (default 0.3)       -> medium (high if twice as much)
#   falling            score fell for                 falling_periods in a row (default 2)     -> medium
#   forecast_low       the forecast is below          forecast_low          (default 3.5)       -> low (medium if 0.5 or more below the limit)
#   forecast_drop      the forecast is lower than the last real score by forecast_drop (0.3)    -> low
#   (a forecast is a prediction, so it alone never gets a high severity)
#   negative_feedback  this share of usable comments is negative (default 40%, 5+ comments)    -> medium
#   category_low       any category is below          category_low          (default 3.0)       -> high
#
# No duplicates: one alert per instructor + period + kind (database rule). Running the analysis
# again updates the text and numbers but KEEPS the status and the administrator's note.
# An alert that is still "new" but no longer true is removed. Reviewed/resolved alerts stay as history.

from django.db import transaction

from ..models import Alert, AlertSettings
from .data import CATEGORIES

MIN_COMMENTS_FOR_ALERT = 5


def evaluate(series, forecast_row, summary, s):
    """Pure rules. Returns {kind: {severity, title, message, evidence}} for the newest period of `series`."""
    if not series:
        return {}
    latest = series[-1]
    previous = series[-2] if len(series) >= 2 else None
    overall = round(latest['overall'], 2)
    found = {}

    if overall < s.low_score:
        found['low_score'] = dict(
            severity='high', title=f"Overall score {overall:.2f} is below {s.low_score:g}",
            message=f"The overall score for {latest['label']} is {overall:.2f} out of 5, which is below the limit of {s.low_score:g}.",
            evidence={'overall': overall, 'limit': s.low_score, 'responses': latest['responses']})

    if previous is not None:
        change = round(overall - previous['overall'], 2)
        if change <= -s.score_drop:
            found['score_drop'] = dict(
                severity='high' if change <= -2 * s.score_drop else 'medium', title=f"Score fell by {abs(change):.2f}",
                message=f"The overall score went from {previous['overall']:.2f} to {overall:.2f} since the period before.",
                evidence={'from': round(previous['overall'], 2), 'to': overall, 'change': change, 'limit': s.score_drop})

    k = int(s.falling_periods)
    if k >= 1 and len(series) >= k + 1:
        tail = [round(x['overall'], 2) for x in series[-(k + 1):]]
        if all(tail[i + 1] < tail[i] for i in range(k)):
            found['falling'] = dict(
                severity='medium', title=f"Score fell {k} periods in a row",
                message="Overall scores: " + " → ".join(f"{v:.2f}" for v in tail) + ".",
                evidence={'scores': tail, 'periods': k})

    if forecast_row is not None and forecast_row.status == 'ok' and forecast_row.predicted_overall is not None:
        predicted = round(forecast_row.predicted_overall, 2)
        if predicted < s.forecast_low:
            found['forecast_low'] = dict(
                severity='medium' if predicted < s.forecast_low - 0.5 else 'low', title=f"Forecast {predicted:.2f} is below {s.forecast_low:g}",
                message=(f"The AI forecast for {forecast_row.target_label or 'the next period'} is {predicted:.2f} "
                         f"(range {forecast_row.range_low:.2f} to {forecast_row.range_high:.2f}). A forecast is a prediction, not a fact."),
                evidence={'predicted': predicted, 'limit': s.forecast_low,
                          'low': forecast_row.range_low, 'high': forecast_row.range_high})
        change = round(predicted - overall, 2)
        if change <= -s.forecast_drop:
            found['forecast_drop'] = dict(
                severity='low', title=f"Forecast is {abs(change):.2f} lower than the last score",
                message=f"Last real score {overall:.2f}; forecast {predicted:.2f}. A forecast is a prediction, not a fact.",
                evidence={'last': overall, 'predicted': predicted, 'change': change, 'limit': s.forecast_drop})

    if summary is not None and not summary.hidden and summary.n_analyzed >= MIN_COMMENTS_FOR_ALERT:
        share = round(summary.negative / summary.n_analyzed * 100)
        if share >= s.negative_share:
            found['negative_feedback'] = dict(
                severity='medium', title=f"{share}% of comments are negative",
                message=f"{summary.negative} of {summary.n_analyzed} usable comments were negative (limit {s.negative_share}%).",
                evidence={'negative': summary.negative, 'analysed': summary.n_analyzed, 'share': share,
                          'limit': s.negative_share})

    low = [(label, round(latest['categories'][f], 2)) for f, label in CATEGORIES
           if latest['categories'][f] < s.category_low]
    if low:
        found['category_low'] = dict(
            severity='high',
            title="Low category: " + ", ".join(label for label, _ in low),
            message="; ".join(f"{label} {value:.2f}" for label, value in low) + f" (limit {s.category_low:g}).",
            evidence={'categories': [{'label': label, 'score': value} for label, value in low], 'limit': s.category_low})
    return found


@transaction.atomic
def sync(instructor, series, forecast_row, summary, run=None):
    """Save the alerts of the newest period. Returns how many alerts are now active for it."""
    if not series:
        return 0
    period = series[-1]['period']
    found = evaluate(series, forecast_row, summary, AlertSettings.get())

    for kind, data in found.items():
        alert, created = Alert.objects.get_or_create(
            instructor=instructor, period=period, kind=kind,
            defaults={**data, 'run': run})
        if not created:
            for key, value in data.items():
                setattr(alert, key, value)
            alert.run = run
            alert.save()

    # still "new" but no longer true -> remove. Reviewed / resolved stay as history.
    Alert.objects.filter(instructor=instructor, period=period, status='new').exclude(kind__in=found).delete()
    return len(found)


def resync_all():
    """Apply the (changed) thresholds to the SAVED results of every active instructor. No model is run."""
    from users.models import User
    from ..models import FeedbackSummary, ForecastResult
    from .data import analyse_history, finished_periods

    periods = finished_periods()
    total = 0
    for teacher in User.objects.filter(role='instructor', is_active=True):
        series, _ = analyse_history(teacher, periods)
        if not series:
            continue
        period = series[-1]['period']
        forecast = ForecastResult.objects.filter(instructor=teacher, based_on_period=period).first()
        summary = FeedbackSummary.objects.filter(instructor=teacher, period=period).first()
        total += sync(teacher, series, forecast, summary)
    return total