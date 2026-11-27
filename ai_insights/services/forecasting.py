# ai_insights/services/forecasting.py
#
# Forecast the NEXT period's score from REAL past scores, with scikit-learn.
#
# How it works (simple words):
#   1. Take the instructor's real score of each finished period (oldest first).
#   2. Pretend to be "in the past" again and predict each period using ONLY
#      the periods before it. This is "walk-forward" testing. It never lets
#      the model see the future, so there is no data leakage.
#   3. Compare 4 simple models by their average error (MAE) and keep the best.
#   4. Train the best model on ALL real periods and predict the next one.
#
# Nothing is hard-coded or invented. With too little history we say so.

import numpy as np
import sklearn
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .. import conf
from .data import CATEGORIES, MODEL_TEXT, TERM_POSITION, hash_of, period_label  # noqa: F401

# Version of THIS forecasting method. Change it whenever the method changes, so old
# saved forecasts are recomputed and the page always shows which version made them.
MODEL_VERSION = conf.FORECAST_MODEL_VERSION

# Simplest first. If two models tie, the earlier (simpler) one wins.
MODEL_ORDER = ['last', 'mean', 'trend', 'trend_term']


def _features(model_name, indexes, term_positions):
    if model_name == 'trend':
        return np.array([[i] for i in indexes], dtype=float)
    return np.array([[i, term_positions[i]] for i in indexes], dtype=float)


def _predict_one(model_name, y, term_positions, target_index):
    """Predict position `target_index` using ONLY y[0:target_index] (the past)."""
    past = list(range(target_index))
    y_past = np.array([y[i] for i in past], dtype=float)

    if model_name == 'last':
        return float(y_past[-1])
    if model_name == 'mean':
        return float(y_past.mean())

    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))     # scaler learns from the past only
    model.fit(_features(model_name, past, term_positions), y_past)
    return float(model.predict(_features(model_name, [target_index], term_positions))[0])


def _clip(value):
    return float(min(5.0, max(1.0, value)))


def walk_forward_predictions(model_name, y, term_positions):
    """
    [(position, predicted)] for each test step. Each guess uses ONLY the periods before it.
    Needs at least MIN_TRAIN past values per step.
    """
    return [
        (k, _clip(_predict_one(model_name, y, term_positions, k)))
        for k in range(conf.MIN_TRAIN, len(y))
    ]


def walk_forward_errors(model_name, y, term_positions):
    """Absolute error of each test step."""
    return [abs(guess - y[k]) for k, guess in walk_forward_predictions(model_name, y, term_positions)]


def next_period_info(last_period):
    """
    The period that comes AFTER the last finished one (it does not have to exist in the database yet).
      Prelims -> Midterm -> Pre-Finals (semi-final) -> Finals -> Prelims of the NEXT semester.
    Returns {"term", "semester", "school_year", "label"}.
    """
    order = ['prelims', 'midterm', 'prefinals', 'finals']
    names = {'prelims': 'Prelims', 'midterm': 'Midterm', 'prefinals': 'Pre-Finals', 'finals': 'Finals'}
    term, semester, year = last_period.term, last_period.semester, last_period.school_year

    if term in order and order.index(term) < len(order) - 1:
        nxt = order[order.index(term) + 1]
        return {"term": nxt, "semester": semester, "school_year": year,
                "label": f"{names[nxt]} · same semester, S.Y. {year}"}

    # last term of the semester -> first term of the next semester
    if semester == '1st':
        return {"term": "prelims", "semester": "2nd", "school_year": year,
                "label": f"Prelims · Second Semester, S.Y. {year}"}
    try:
        a, b = year.split('-')
        new_year = f"{int(a) + 1}-{int(b) + 1}"
        return {"term": "prelims", "semester": "1st", "school_year": new_year,
                "label": f"Prelims · First Semester, S.Y. {new_year}"}
    except (ValueError, AttributeError):
        return {"term": "prelims", "semester": "1st", "school_year": "",
                "label": "Prelims · next school year"}


def next_period_label(last_period):
    """Words for the next period (see next_period_info)."""
    return next_period_info(last_period)["label"]


def _base(series, quality):
    """The part every result has: the REAL history that was used, and what was left out."""
    return {
        "data_quality": quality or {},
        "periods_used": len(series),
        "history": [
            {"label": s["label"], "overall": round(s["overall"], 2), "responses": s["responses"]}
            for s in series
        ],
        "library_version": f"scikit-learn {sklearn.__version__}",
    }


def blocked_result(series, quality, message):
    """A 'not enough data' result for a reason the caller knows (for example: the chosen period is unusable)."""
    result = _base(series, quality)
    result.update(status='insufficient', message=message)
    return result


def make_forecast(series, quality=None):
    """
    `series` and `quality` come from data.analyse_history(). Returns a plain dict. `status` is:
      'insufficient' - not enough usable periods (or none at all). No number.
      'baseline'     - exactly ONE usable period: a starting point, NOT enough to forecast. No number.
      'unreliable'   - enough periods, but the model's own test error is too big. No number.
      'ok'           - a forecast, with its range, test error and limits.
    """
    n = len(series)
    base = _base(series, quality)

    skipped = (quality or {}).get("skipped") or []
    skipped_note = (f" {len(skipped)} other period(s) were left out because of too few responses or "
                    f"missing scores." if skipped else "")

    if n == 0:
        base.update(
            status='insufficient',
            message=(f"No finished period has at least {conf.MIN_RESPONSES} responses yet, so there is "
                     f"nothing to learn from.{skipped_note}"),
        )
        return base

    if n == 1:
        only = series[0]
        base.update(
            status='baseline',
            message=(
                f"Baseline only: one finished period ({only['label']}) is a starting point, not enough to "
                f"forecast. Its real score is {only['overall']:.2f} from {only['responses']} responses. "
                f"At least {conf.MIN_PERIODS} periods with {conf.MIN_RESPONSES}+ responses are needed."
                f"{skipped_note}"
            ),
        )
        return base

    if n < conf.MIN_PERIODS:
        base.update(
            status='insufficient',
            message=(
                f"Not enough history for a reliable forecast: {n} finished period(s) with at least "
                f"{conf.MIN_RESPONSES} responses, but at least {conf.MIN_PERIODS} are needed.{skipped_note}"
            ),
        )
        return base

    y = [s["overall"] for s in series]
    terms = [TERM_POSITION.get(s["period"].term, 0) for s in series]

    # --- compare the models with walk-forward testing ---
    scores = {}
    for name in MODEL_ORDER:
        errors = walk_forward_errors(name, y, terms)
        if errors:
            scores[name] = float(np.mean(errors))

    # 'trend_term' needs enough different data to make sense; keep it only with 5+ periods
    if n < 5:
        scores.pop('trend_term', None)

    best = min(MODEL_ORDER, key=lambda m: (scores.get(m, 99.0), MODEL_ORDER.index(m)))
    test_guesses = walk_forward_predictions(best, y, terms)
    steps = len(test_guesses)
    backtest = [
        {"label": series[k]["label"], "actual": round(y[k], 2), "predicted": round(guess, 2)}
        for k, guess in test_guesses
    ]
    mae = scores[best]
    baseline = scores['last']

    # --- is the model good enough to be shown at all? ---
    limit = conf.max_validation_mae()
    if mae > limit:
        base.update(
            status='unreliable',
            message=(
                f"The forecast was not shown because the model's own test error ({mae:.2f} points) is above "
                f"the allowed limit ({limit:.2f}). The scores of this instructor change too much from one "
                f"period to the next to predict reliably."
            ),
            model_name=best, model_version=MODEL_VERSION, backtest=[],
            validation_mae=round(mae, 3), baseline_mae=round(baseline, 3), validation_steps=steps,
        )
        return base

    # --- final prediction: learn from ALL real periods, predict the next one ---
    y_ext = y + [y[-1]]                       # placeholder so the helper can predict index n
    terms_next = terms + [_next_term_position(series[-1]["period"].term)]
    overall = _clip(_predict_one(best, y_ext, terms_next, n))

    width = max(mae, 0.05)
    categories = {}
    for field, label in CATEGORIES:
        cy = [s["categories"][field] for s in series]
        cy_ext = cy + [cy[-1]]
        value = _clip(_predict_one(best, cy_ext, terms_next, n))
        cerr = walk_forward_errors(best, cy, terms)
        cmae = float(np.mean(cerr)) if cerr else None
        cwidth = max(cmae or 0.0, 0.05)
        categories[field] = {
            "label": label,
            "value": round(value, 2),
            "low": round(_clip(value - cwidth), 2),
            "high": round(_clip(value + cwidth), 2),
            "mae": None if cmae is None else round(cmae, 3),
            "last_actual": round(cy[-1], 2),
        }

    last = series[-1]
    diff = overall - last["overall"]
    if abs(diff) < conf.STEADY_BAND:
        direction = 'steady'
    else:
        direction = 'up' if diff > 0 else 'down'

    base.update(
        status='ok',
        message="",
        target_label=next_period_label(last["period"]),
        target=next_period_info(last["period"]),
        based_on_period=last["period"],
        predicted_overall=round(overall, 2),
        range_low=round(_clip(overall - width), 2),
        range_high=round(_clip(overall + width), 2),
        predicted_categories=categories,
        model_name=best,
        model_version=MODEL_VERSION,
        backtest=backtest,
        validation_mae=round(mae, 3),
        baseline_mae=round(baseline, 3),
        validation_steps=steps,
        reliability='low' if n < conf.LOW_RELIABILITY_BELOW else 'fair',
        direction=direction,
        change_from_last=round(diff, 2),
        all_model_errors={k: round(v, 3) for k, v in scores.items()},
    )
    return base


def _next_term_position(term):
    pos = TERM_POSITION.get(term, 0)
    return 1 if pos in (0, 4) else pos + 1


def forecast_fingerprint(series):
    return hash_of(
        MODEL_VERSION, conf.MIN_PERIODS, conf.MIN_TRAIN, conf.max_validation_mae(), sklearn.__version__,
        [(s["period"].pk, s["responses"], round(s["overall"], 4),
          sorted(s["categories"].items())) for s in series],
    )