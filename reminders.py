"""Background Web Push reminder delivery.

Run ``python reminders.py`` from a minute-based cron or worker. VAPID values
are read from VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, and VAPID_SUBJECT.
"""

import json
import os
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from progress import _completion_counts, current_break, is_scheduled_on

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _vapid_private_key(value):
    """Return key contents, allowing a path in environment-based deployments."""
    if not value:
        return value
    key_path = Path(value)
    if not key_path.is_absolute():
        key_path = Path(__file__).resolve().parent / key_path
    if key_path.is_file():
        return key_path.read_text(encoding="utf-8")
    return value


def _timezone(name):
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return timezone.utc


def format_reminder_schedule(habit):
    """Return a short, user-facing description of a saved reminder schedule."""
    reminder_time = habit.get("reminder_time")
    if not reminder_time:
        return ""
    try:
        display_time = datetime.strptime(reminder_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        display_time = reminder_time

    frequency = habit.get("frequency")
    if frequency == "weekly" and habit.get("reminder_weekdays"):
        days = [
            day.capitalize()
            for day in WEEKDAYS
            if day in habit["reminder_weekdays"]
        ]
        if days:
            schedule = (
                ", ".join(days[:-1]) + f" and {days[-1]}"
                if len(days) > 1
                else days[0]
            )
            return f"{schedule} at {display_time}"
    if frequency == "monthly" and habit.get("reminder_month_day") is not None:
        month_day = habit["reminder_month_day"]
        day_description = (
            "the last day of the month" if month_day == "last" else f"day {month_day} of each month"
        )
        return f"{day_description} at {display_time}"
    if frequency == "daily":
        return f"Every day at {display_time}"
    return f"{frequency.capitalize()} at {display_time}"


def validate_reminder_settings(
    frequency,
    enabled,
    reminder_time,
    reminder_timezone,
    reminder_weekdays=None,
    reminder_month_day=None,
    allow_legacy_schedule=False,
):
    """Validate and normalize the reminder settings submitted by a habit form."""
    if not enabled:
        return [], [], None

    errors = []
    try:
        datetime.strptime(reminder_time, "%H:%M")
    except (TypeError, ValueError):
        errors.append("Enter a valid reminder time.")

    try:
        ZoneInfo(reminder_timezone)
    except (TypeError, ZoneInfoNotFoundError):
        errors.append("Choose a valid time zone.")

    weekdays = list(dict.fromkeys(reminder_weekdays or []))
    month_day = reminder_month_day
    if frequency == "weekly":
        if (not weekdays and not allow_legacy_schedule) or any(
            day not in WEEKDAYS for day in weekdays
        ):
            errors.append("Choose at least one valid weekday for weekly reminders.")
    elif frequency == "monthly":
        if month_day != "last":
            try:
                month_day = int(month_day)
            except (TypeError, ValueError):
                month_day = 0
            if not 1 <= month_day <= 28:
                errors.append("Choose a date from 1 to 28 or the last day of the month.")
    else:
        month_day = None

    if frequency != "weekly":
        weekdays = []
    if frequency != "monthly":
        month_day = None
    return errors, weekdays, month_day


def _period_start(frequency, current):
    if frequency == "weekly":
        return current - timedelta(days=current.weekday())
    if frequency == "monthly":
        return current.replace(day=1)
    return current


def _goal_met(habit, local_date):
    """Use the same Monday/month-start boundaries as the progress tracker."""
    counts = _completion_counts(habit)
    frequency = habit.get("frequency") or "daily"
    start = _period_start(frequency, local_date)
    period_count = sum(
        counts.get(start + timedelta(days=offset), 0)
        for offset in range((local_date - start).days + 1)
    )
    target = max(int(habit.get("target", 1)), 1)
    if frequency in ("weekly", "monthly"):
        return period_count >= target
    return counts.get(local_date, 0) >= target


def _scheduled_for_reminder(habit, local_date):
    frequency = habit.get("frequency")
    weekdays = habit.get("reminder_weekdays")
    if frequency == "weekly" and weekdays:
        return WEEKDAYS[local_date.weekday()] in weekdays
    if frequency == "monthly" and habit.get("reminder_month_day") is not None:
        month_day = habit.get("reminder_month_day")
        if month_day == "last":
            return local_date.day == monthrange(local_date.year, local_date.month)[1]
        try:
            return local_date.day == int(month_day)
        except (TypeError, ValueError):
            return False
    # Legacy records without explicit schedule fields retain their old schedule.
    return is_scheduled_on(habit, local_date)


def reminder_due(habit, now_utc=None):
    """Return the local occurrence key when a reminder is due, otherwise None."""
    if not habit.get("reminder_enabled", True):
        return None
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(_timezone(habit.get("reminder_timezone")))
    try:
        reminder_time = datetime.strptime(habit.get("reminder_time", ""), "%H:%M").time()
    except (TypeError, ValueError):
        return None

    reminder_datetime = datetime.combine(local_now.date(), reminder_time)
    reminder_datetime = reminder_datetime.replace(tzinfo=local_now.tzinfo)
    if local_now < reminder_datetime:
        return None

    occurrence_date = local_now.date()
    if not _scheduled_for_reminder(habit, occurrence_date):
        return None
    if habit.get("reminder_enabled") is True:
        if _goal_met(habit, occurrence_date):
            return None
        if habit.get("frequency") in ("weekly", "monthly") and _completion_counts(
            habit
        ).get(occurrence_date, 0):
            return None
    # Breaks use UTC dates like the dashboard; checking the local date too keeps the
    # whole break quiet in the user's own time zone.
    utc_date = now_utc.astimezone(timezone.utc).date()
    if current_break(habit, occurrence_date) or current_break(habit, utc_date):
        return None
    return f"{occurrence_date.isoformat()}:{habit.get('reminder_time')}"


def send_due_reminders(database, now_utc=None):
    """Send due reminders once and return the number sent.

    Delivery records are inserted before sending with a unique key. A failed
    send removes that record so a later scheduler run can retry.
    """
    from pymongo.errors import DuplicateKeyError

    public_key = os.getenv("VAPID_PUBLIC_KEY")
    private_key = _vapid_private_key(os.getenv("VAPID_PRIVATE_KEY"))
    subject = os.getenv("VAPID_SUBJECT")
    push_enabled = bool(public_key and private_key and subject)
    if push_enabled:
        from pywebpush import WebPushException, webpush
    else:
        print("Push notifications are not configured; bell notifications will still be created.")

    deliveries = database["reminder_deliveries"]
    deliveries.create_index("delivery_key", unique=True)
    notifications = database["notifications"]
    notifications.create_index(
        [("user_email", 1), ("habit_id", 1), ("occurrence_key", 1)],
        unique=True,
        name="notification_occurrence_unique",
    )
    sent = 0
    habits = database["habits"].find({"reminder_enabled": True})
    for habit in habits:
        occurrence_key = reminder_due(habit, now_utc)
        if not occurrence_key:
            continue
        notification = {
            "user_email": habit.get("user_email"),
            "habit_id": habit["_id"],
            "habit_name": habit["name"],
            "message": f"Reminder: {habit['name']} is scheduled now.",
            "url": "/dashboard",
            "occurrence_key": occurrence_key,
            "reminder_date": occurrence_key.split(":", 1)[0],
            "reminder_time": habit.get("reminder_time"),
            "created_at": datetime.utcnow(),
        }
        try:
            notifications.insert_one(notification)
        except DuplicateKeyError:
            pass

        if not push_enabled:
            continue
        subscription = database["push_subscriptions"].find_one(
            {"user_email": habit.get("user_email")}
        )
        if not subscription:
            print(f"No push subscription found for {habit.get('user_email')}.")
            continue

        delivery_key = f"{habit['_id']}:{occurrence_key}"
        try:
            deliveries.insert_one({"delivery_key": delivery_key, "created_at": datetime.utcnow()})
        except DuplicateKeyError:
            continue

        try:
            webpush(
                subscription_info=subscription["subscription"],
                data=json.dumps(
                    {
                        "title": "Habit reminder",
                        "body": habit["name"],
                        "url": f"/dashboard?habit={habit['_id']}",
                    }
                ),
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
            )
        except (WebPushException, OSError, TypeError, ValueError) as error:
            deliveries.delete_one({"delivery_key": delivery_key})
            print(f"Push delivery failed for {habit.get('user_email')}: {error}")
            continue
        sent += 1
    return sent


if __name__ == "__main__":
    from app import get_db

    send_due_reminders(get_db())
