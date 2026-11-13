# ai_insights/services/notices.py
#
# Short notices for ONE instructor about their OWN results (never about anyone else).
#   ready  - a new AI analysis was made for the period
#   up     - the overall score went up by 0.1 or more since the period before
#   down   - the overall score went down by 0.1 or more
# The numbers are real scores. Words are kind and plain. One notice per kind per period.
# An instructor sees a notice only after the official analysis is finished (see selectors).

from django.utils import timezone

from ..models import InstructorNotice

CHANGE_LIMIT = 0.1


def sync(instructor, series):
    if not series:
        return 0
    latest = series[-1]
    period = latest['period']
    wanted = {'ready': ("A new AI analysis is ready",
                        f"Your results for {latest['label']} were analysed. Open Forecast and Insights to read them.")}
    if len(series) >= 2:
        change = round(latest['overall'] - series[-2]['overall'], 2)
        if change >= CHANGE_LIMIT:
            wanted['up'] = (f"Your overall score went up by {change:.2f}",
                            f"From {series[-2]['overall']:.2f} to {latest['overall']:.2f} out of 5. Well done.")
        elif change <= -CHANGE_LIMIT:
            wanted['down'] = (f"Your overall score went down by {abs(change):.2f}",
                              f"From {series[-2]['overall']:.2f} to {latest['overall']:.2f} out of 5. "
                              f"Open Insights to see which areas students mention.")

    for kind, (title, message) in wanted.items():
        notice, created = InstructorNotice.objects.get_or_create(
            instructor=instructor, period=period, kind=kind, defaults={'title': title, 'message': message})
        if not created and (notice.title, notice.message) != (title, message):
            notice.title, notice.message = title, message
            notice.created_at, notice.read_at = timezone.now(), None      # new words = new notice
            notice.save()
        elif not created and kind == 'ready':
            notice.created_at = timezone.now()                            # a new analysis was made
            notice.read_at = None
            notice.save()
    InstructorNotice.objects.filter(instructor=instructor, period=period).exclude(kind__in=wanted).delete()
    return len(wanted)