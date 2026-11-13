# ai_insights/charts.py
#
# Draws simple, easy-to-read LINE charts as SVG on the SERVER. No chart library and no internet
# are needed, so the chart always shows. Every point has a tooltip, and the page also keeps a
# data table for screen readers.

import math

PALETTE = ['#1f3b8a', '#c9a227', '#2f7d4f', '#b4532a', '#7b4fa3', '#3a8fb7']

W, H = 720, 350
LEFT, RIGHT, TOP, BOTTOM = 86, 16, 16, 112


def short_period(period):
    """'Midterm · 2nd 24-25' : short enough for an axis label."""
    sy = str(period.school_year)
    years = sy.split('-')
    sy_short = f"{years[0][-2:]}-{years[1][-2:]}" if len(years) == 2 else sy
    semester = period.get_semester_display().replace('First Semester', '1st Sem').replace('Second Semester', '2nd Sem')
    return f"{period.get_term_display()} · {semester} {sy_short}"


def _axis(values):
    nums = [v for v in values if v is not None]
    if not nums:
        return 1.0, 5.0
    lo = max(1.0, math.floor((min(nums) - 0.25) * 2) / 2)
    hi = min(5.0, math.ceil((max(nums) + 0.25) * 2) / 2)
    if hi - lo < 1.0:
        if hi >= 5.0:
            lo = max(1.0, hi - 1.0)
        else:
            hi = min(5.0, lo + 1.0)
    return lo, hi


def line_chart(labels, series, forecast=None):
    """
    labels:  x labels, oldest first.
    series:  [{'name': 'Punctuality', 'values': [4.1, None, 3.9, ...], 'dashed': False}]
             None = no data for that period (the line breaks there).
    Returns a plain dict for the template, or None when there is nothing to draw.
    """
    if not labels or not any(v is not None for s in series for v in s['values']):
        return None
    lo, hi = _axis([v for s in series for v in s['values']])
    pw, ph = W - LEFT - RIGHT, H - TOP - BOTTOM
    n = len(labels)

    def x_at(i):
        return LEFT + (pw / 2 if n == 1 else pw * i / (n - 1))

    def y_at(v):
        return TOP + ph * (1 - (v - lo) / (hi - lo))

    ticks, t = [], lo
    while t <= hi + 1e-9:
        ticks.append({'y': round(y_at(t), 1), 'label': f"{t:g}"})
        t += 0.5 if hi - lo <= 2.5 else 1.0

    step = max(1, math.ceil(n / 6))                      # never more than ~6 labels
    xlabels = [{'x': round(x_at(i), 1), 'text': labels[i], 'show': i % step == 0 or i == n - 1}
               for i in range(n)]

    lines, table = [], []
    for k, s in enumerate(series):
        color = s.get('color') or PALETTE[k % len(PALETTE)]
        segments, current, dots = [], [], []
        for i, v in enumerate(s['values']):
            if v is None:
                if current:
                    segments.append(current)
                current = []
                continue
            x, y = round(x_at(i), 1), round(y_at(v), 1)
            current.append((x, y))
            dots.append({'x': x, 'y': y, 'tip': f"{s['name']} · {labels[i]}: {v:.2f}"})
        if current:
            segments.append(current)
        paths = [" ".join(f"{'M' if j == 0 else 'L'}{x},{y}" for j, (x, y) in enumerate(seg))
                 for seg in segments if len(seg) > 1]
        lines.append({'name': s['name'], 'color': color, 'paths': paths, 'dots': dots,
                      'dashed': bool(s.get('dashed')), 'last': s['values'][-1] if s['values'] else None})
        table.append({'name': s['name'], 'values': [None if v is None else round(v, 2) for v in s['values']]})

    return {'w': W, 'h': H, 'left': LEFT, 'right': W - RIGHT, 'bottom': H - BOTTOM, 'top': TOP,
            'ticks': ticks, 'xlabels': xlabels, 'lines': lines, 'labels': labels, 'table': table,
            'lo': lo, 'hi': hi, 'label_y': H - BOTTOM + 16}


# ─────────────────────────────────────────────
# RADAR chart (the five categories on a pentagon). Also drawn on the server as SVG.
# ─────────────────────────────────────────────
RADAR_SIZE = 380
RADAR_RADIUS = 120


def radar_chart(axes, series, lo=1.0, hi=5.0):
    """
    axes:   ['Teaching Effectiveness', ...]  (3 or more names)
    series: [{'name': 'This instructor', 'values': [4.1, 3.8, ...], 'dashed': False, 'color': '#..'}]
            A value of None is drawn at the centre (no data for that axis).
    Returns a dict for the template, or None when nothing can be drawn.
    """
    n = len(axes)
    if n < 3 or not any(v is not None for s in series for v in s['values']):
        return None
    cx = cy = RADAR_SIZE / 2

    def point(i, fraction):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        return (round(cx + RADAR_RADIUS * fraction * math.cos(angle), 1),
                round(cy + RADAR_RADIUS * fraction * math.sin(angle), 1))

    def frac(v):
        return 0.0 if v is None else max(0.0, min(1.0, (v - lo) / (hi - lo)))

    rings = []
    for step in range(1, 5):                                  # 4 rings: 2, 3, 4, 5
        value = lo + (hi - lo) * step / 4
        pts = " ".join(f"{x},{y}" for x, y in (point(i, step / 4) for i in range(n)))
        rings.append({'points': pts, 'label': f"{value:g}", 'y': point(0, step / 4)[1]})
    spokes = []
    for i, name in enumerate(axes):
        x, y = point(i, 1.0)
        lx, ly = point(i, 1.22)
        anchor = 'middle' if abs(lx - cx) < 8 else ('start' if lx > cx else 'end')
        spokes.append({'x': x, 'y': y, 'lx': lx, 'ly': ly, 'anchor': anchor, 'text': name})
    shapes, table = [], []
    for k, s in enumerate(series):
        color = s.get('color') or PALETTE[k % len(PALETTE)]
        pts = [point(i, frac(v)) for i, v in enumerate(s['values'])]
        shapes.append({'name': s['name'], 'color': color, 'dashed': bool(s.get('dashed')),
                       'points': " ".join(f"{x},{y}" for x, y in pts),
                       'dots': [{'x': x, 'y': y, 'tip': f"{s['name']} · {axes[i]}: {s['values'][i]:.2f}"}
                                for i, (x, y) in enumerate(pts) if s['values'][i] is not None]})
        table.append({'name': s['name'], 'values': [None if v is None else round(v, 2) for v in s['values']]})
    return {'size': RADAR_SIZE, 'cx': cx, 'cy': cy, 'rings': rings, 'spokes': spokes, 'shapes': shapes,
            'axes': axes, 'table': table}