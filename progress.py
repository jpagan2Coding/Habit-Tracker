"""Overall habit progress calculations.

The existing habit schema records completed dates, so those dates remain
supported. Newer records may use ``completion_history`` entries shaped like
``{"date": "YYYY-MM-DD", "status": "completed"}``.

An occurrence is scheduled on every day for daily habits, every seventh day
anchored to the habit creation date for weekly habits, and the matching day
of each month for monthly habits (clamped to the last day when needed).
``target`` is the number of occurrences in each frequency cycle. Completed
occurrences count toward the numerator. Skipped, missed, and undone statuses
remain in the denominator and count as incomplete; an undone record supersedes
an earlier completion for the same date.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta

VALID_PROGRESS_PERIODS = (7, 30, 90)
COMPLETION_STATUSES = {"completed", "skipped", "missed", "undone"}


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None


def _scheduled_dates(habit, start_date, end_date):
    anchor = _as_date(habit.get("created_at")) or start_date
    first = max(start_date, anchor)
    frequency = habit.get("frequency")
    current = first
    dates = []
    while current <= end_date:
        days_since_anchor = (current - anchor).days
        is_scheduled = (
            frequency == "daily"
            or frequency == "weekly" and days_since_anchor % 7 == 0
            or frequency == "monthly"
            and current.day == min(anchor.day, monthrange(current.year, current.month)[1])
        )
        if is_scheduled:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def is_scheduled_on(habit, on_date):
    """Return whether a habit has a scheduled occurrence on one date."""
    return bool(_scheduled_dates(habit, on_date, on_date))


def _completion_counts(habit):
    counts = {}
    history = habit.get("completion_history") or []
    for record in history:
        if not isinstance(record, dict):
            continue
        record_date = _as_date(record.get("date"))
        status = record.get("status", "completed")
        if record_date is None or status not in COMPLETION_STATUSES:
            continue
        try:
            count = int(record.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        counts[record_date] = count if status == "completed" else 0

    for completed_date in habit.get("completed_dates", []):
        parsed_date = _as_date(completed_date)
        if parsed_date is not None and parsed_date not in counts:
            counts[parsed_date] = 1
    return counts


def calculate_overall_progress(habits, end_date=None, period_days=7):
    """Return scheduled, completed, and percentage values for a date period."""
    if period_days not in VALID_PROGRESS_PERIODS:
        raise ValueError("period_days must be one of 7, 30, or 90")

    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=period_days - 1)
    scheduled = 0
    completed = 0

    for habit in habits:
        target = max(int(habit.get("target", 1)), 1)
        completion_counts = _completion_counts(habit)
        for occurrence_date in _scheduled_dates(habit, start_date, end_date):
            scheduled += target
            completed += min(completion_counts.get(occurrence_date, 0), target)

    percentage = round(completed / scheduled * 100) if scheduled else 0
    return {
        "period_days": period_days,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "scheduled": scheduled,
        "completed": completed,
        "percentage": percentage,
    }
