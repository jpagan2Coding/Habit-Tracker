from datetime import datetime, timezone

from reminders import reminder_due


def test_daily_reminder_is_due_at_local_time():
    habit = {
        "frequency": "daily",
        "created_at": datetime(2026, 10, 1),
        "reminder_time": "09:30",
        "reminder_timezone": "America/New_York",
    }
    now = datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc)
    assert reminder_due(habit, now) == "2026-11-03:09:30"


def test_reminder_is_not_due_at_another_time_or_unscheduled_day():
    habit = {
        "frequency": "weekly",
        "created_at": datetime(2026, 11, 3),
        "reminder_time": "09:30",
        "reminder_timezone": "UTC",
    }
    assert reminder_due(habit, datetime(2026, 11, 3, 9, 29, tzinfo=timezone.utc)) is None
    assert reminder_due(habit, datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)) is None


def test_monthly_reminder_uses_habit_schedule():
    habit = {
        "frequency": "monthly",
        "created_at": datetime(2026, 1, 31),
        "reminder_time": "08:00",
        "reminder_timezone": "UTC",
    }
    assert reminder_due(habit, datetime(2026, 2, 28, 8, tzinfo=timezone.utc)) == "2026-02-28:08:00"
