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
    anchor = _as_date(habit.get("start_date")) or _as_date(habit.get("created_at")) or start_date
    first = max(start_date, anchor)
    frequency = habit.get("frequency")
    current = first
    dates = []
    while current <= end_date:
        days_since_anchor = (current - anchor).days
        is_scheduled = (
            frequency == "daily"
            or frequency == "weekly"
            and days_since_anchor % 7 == 0
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


# Per-habit summary metrics.
#
# A habit is measured in periods that match its frequency: days, Monday-start
# weeks, or calendar months (the same periods the "Today's Habits" list uses).
# A period is "met" once its target is reached. Team rule: a skipped day neither
# breaks nor extends a streak, so each skip lowers that period's target by one,
# and a period whose target was skipped away entirely is passed over. The
# current period never breaks a streak while it is still in progress.

PERIOD_UNITS = {"daily": "day", "weekly": "week", "monthly": "month"}


def period_start(frequency, day):
    """Return the first day of the day, week (Monday), or month containing ``day``."""
    if frequency == "weekly":
        return day - timedelta(days=day.weekday())
    if frequency == "monthly":
        return day.replace(day=1)
    return day


def _next_period_start(frequency, start):
    if frequency == "weekly":
        return start + timedelta(days=7)
    if frequency == "monthly":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=1)


def _frequency(habit):
    frequency = habit.get("frequency")
    return frequency if frequency in PERIOD_UNITS else "daily"


def _skipped_dates(habit):
    skipped = {_as_date(value) for value in habit.get("skipped_dates") or []}
    for record in habit.get("completion_history") or []:
        if isinstance(record, dict) and record.get("status") == "skipped":
            skipped.add(_as_date(record.get("date")))
    skipped.discard(None)
    return skipped


def _totals_by_period(frequency, day_counts):
    totals = {}
    for day, count in day_counts:
        key = period_start(frequency, day)
        totals[key] = totals.get(key, 0) + count
    return totals


def habit_periods(habit, today):
    """Return one entry per period, oldest first, up to the period containing ``today``.

    Periods start from the one the habit began in (its creation date, or its
    earliest logged day if that is older). Each entry has ``start``, ``end``,
    ``completed``, ``skipped``, ``target``, and a ``status`` of ``met``,
    ``missed``, ``skipped`` (every needed occurrence was skipped), or
    ``in_progress`` (the current period, target not reached yet).
    """
    frequency = _frequency(habit)
    target = max(int(habit.get("target") or 1), 1)
    counts = _completion_counts(habit)
    # a completion replaces a skip on the same day
    skipped_days = {day for day in _skipped_dates(habit) if not counts.get(day)}
    completed_by_period = _totals_by_period(frequency, counts.items())
    skipped_by_period = _totals_by_period(frequency, ((day, 1) for day in skipped_days))

    logged_days = [day for day, count in counts.items() if count] + list(skipped_days)
    first_day = min([_as_date(habit.get("created_at")) or today, today, *logged_days])
    start = period_start(frequency, first_day)
    current = period_start(frequency, today)

    periods = []
    while start <= current:
        next_start = _next_period_start(frequency, start)
        completed = completed_by_period.get(start, 0)
        skipped = skipped_by_period.get(start, 0)
        needed = target - skipped
        if completed and completed >= needed:
            status = "met"
        elif needed <= 0:
            status = "skipped"
        elif start == current:
            status = "in_progress"
        else:
            status = "missed"
        periods.append(
            {
                "start": start,
                "end": next_start - timedelta(days=1),
                "completed": completed,
                "skipped": skipped,
                "target": target,
                "status": status,
            }
        )
        start = next_start
    return periods


def streaks(periods):
    """Return ``(current, longest)`` streak lengths, counted in periods."""
    current = longest = 0
    for period in periods:
        if period["status"] == "met":
            current += 1
            longest = max(longest, current)
        elif period["status"] == "missed":
            current = 0
    return current, longest


def habit_summary(habit, today, period_days=7):
    """Return the streaks and completion rate shown for one habit on the summary page.

    ``has_history`` stays False until the habit has been logged at least once or
    one full period has gone by since it started. The completion rate covers the
    periods overlapping the last ``period_days`` days: skipped occurrences count
    as incomplete (as in overall progress), and the current period only counts
    once its target is met, so an unfinished day or week isn't held against you.
    """
    if period_days not in VALID_PROGRESS_PERIODS:
        raise ValueError("period_days must be one of 7, 30, or 90")

    periods = habit_periods(habit, today)
    current_streak, longest_streak = streaks(periods)
    window_start = today - timedelta(days=period_days - 1)
    recent = [period for period in periods if period["end"] >= window_start]
    counted = [period for period in recent if period["status"] != "in_progress"]
    expected = sum(period["target"] for period in counted)
    completed = sum(min(period["completed"], period["target"]) for period in counted)

    return {
        "unit": PERIOD_UNITS[_frequency(habit)],
        "has_history": len(periods) > 1
        or any(period["completed"] or period["skipped"] for period in periods),
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "completed": completed,
        "expected": expected,
        "percentage": round(completed / expected * 100) if expected else None,
        "periods": recent,
    }
