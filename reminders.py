"""Background Web Push reminder delivery.

Run ``python reminders.py`` from a minute-based cron or worker. VAPID values
are read from VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, and VAPID_SUBJECT.
"""

import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from progress import is_scheduled_on


def _timezone(name):
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return timezone.utc


def reminder_due(habit, now_utc=None):
    """Return the local occurrence key when a reminder is due, otherwise None."""
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(_timezone(habit.get("reminder_timezone")))
    if habit.get("reminder_time") != local_now.strftime("%H:%M"):
        return None

    occurrence_date = local_now.date()
    if not is_scheduled_on(habit, occurrence_date):
        return None
    return f"{occurrence_date.isoformat()}:{habit.get('reminder_time')}"


def send_due_reminders(database, now_utc=None):
    """Send due reminders once and return the number sent.

    Delivery records are inserted before sending with a unique key. A failed
    send removes that record so a later scheduler run can retry.
    """
    from pymongo.errors import DuplicateKeyError
    public_key = os.getenv("VAPID_PUBLIC_KEY")
    private_key = os.getenv("VAPID_PRIVATE_KEY")
    subject = os.getenv("VAPID_SUBJECT")
    push_enabled = bool(public_key and private_key and subject)
    if push_enabled:
        from pywebpush import WebPushException, webpush

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
        except WebPushException:
            deliveries.delete_one({"delivery_key": delivery_key})
            continue
        sent += 1
    return sent


if __name__ == "__main__":
    from app import get_db

    send_due_reminders(get_db())
