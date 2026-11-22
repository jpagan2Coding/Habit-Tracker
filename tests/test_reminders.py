from datetime import datetime, timezone

from reminders import (
    _vapid_private_key,
    reminder_due,
    send_due_reminders,
    validate_reminder_settings,
)


def test_daily_reminder_is_due_at_local_time():
    habit = {
        "frequency": "daily",
        "created_at": datetime(2026, 10, 1),
        "reminder_time": "09:30",
        "reminder_timezone": "America/New_York",
    }
    now = datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc)
    assert reminder_due(habit, now) == "2026-11-03:09:30"


def test_disabled_reminder_is_never_due():
    habit = {
        "frequency": "daily",
        "reminder_enabled": False,
        "reminder_time": "09:30",
        "reminder_timezone": "UTC",
    }
    assert reminder_due(habit, datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)) is None


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


def test_vapid_private_key_accepts_repository_relative_path(tmp_path, monkeypatch):
    key_file = tmp_path / "private.pem"
    key_file.write_text("test-key", encoding="utf-8")
    monkeypatch.setattr("reminders.__file__", str(tmp_path / "reminders.py"))

    assert _vapid_private_key("private.pem") == "test-key"


def test_vapid_private_key_preserves_inline_key():
    assert _vapid_private_key("inline-key") == "inline-key"


def test_weekly_reminder_uses_any_selected_weekday_and_timezone():
    habit = {
        "frequency": "weekly",
        "target": 3,
        "reminder_enabled": True,
        "reminder_time": "09:30",
        "reminder_timezone": "America/New_York",
        "reminder_weekdays": ["monday", "friday"],
        "created_at": datetime(2026, 11, 1),
    }
    friday = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)
    thursday = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
    assert reminder_due(habit, friday) == "2026-11-06:09:30"
    assert reminder_due(habit, thursday) is None


def test_monthly_last_day_uses_leap_year_calendar():
    habit = {
        "frequency": "monthly",
        "target": 1,
        "reminder_enabled": True,
        "reminder_time": "08:00",
        "reminder_timezone": "UTC",
        "reminder_month_day": "last",
        "created_at": datetime(2026, 1, 1),
    }
    assert reminder_due(habit, datetime(2028, 2, 29, 8, tzinfo=timezone.utc))
    assert reminder_due(habit, datetime(2028, 2, 28, 8, tzinfo=timezone.utc)) is None


def test_reminders_skip_completed_goals_and_weekly_same_day_logs():
    daily = {
        "frequency": "daily",
        "target": 1,
        "reminder_enabled": True,
        "reminder_time": "09:30",
        "reminder_timezone": "UTC",
        "completed_dates": ["2026-11-03"],
        "created_at": datetime(2026, 10, 1),
    }
    weekly = {
        "frequency": "weekly",
        "target": 3,
        "reminder_enabled": True,
        "reminder_time": "09:30",
        "reminder_timezone": "UTC",
        "reminder_weekdays": ["tuesday"],
        "completed_dates": ["2026-11-03"],
        "created_at": datetime(2026, 10, 1),
    }
    now = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    assert reminder_due(daily, now) is None
    assert reminder_due(weekly, now) is None


def test_daily_reminder_stays_due_after_scheduled_time():
    habit = {
        "frequency": "daily",
        "target": 1,
        "reminder_enabled": True,
        "reminder_time": "09:30",
        "reminder_timezone": "UTC",
        "created_at": datetime(2026, 10, 1),
    }

    before = datetime(2026, 11, 3, 9, 29, tzinfo=timezone.utc)
    due = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    later = datetime(2026, 11, 3, 18, 45, tzinfo=timezone.utc)

    assert reminder_due(habit, before) is None
    assert reminder_due(habit, due) == "2026-11-03:09:30"
    assert reminder_due(habit, later) == "2026-11-03:09:30"


def test_reminder_does_not_run_before_configured_local_time():
    habit = {
        "frequency": "daily",
        "target": 1,
        "reminder_enabled": True,
        "reminder_time": "01:15",
        "reminder_timezone": "UTC",
        "created_at": datetime(2026, 10, 1),
    }

    one_minute_before = datetime(2026, 11, 3, 1, 14, tzinfo=timezone.utc)
    exactly_on_time = datetime(2026, 11, 3, 1, 15, tzinfo=timezone.utc)
    after_time = datetime(2026, 11, 3, 1, 16, tzinfo=timezone.utc)

    assert reminder_due(habit, one_minute_before) is None
    assert reminder_due(habit, exactly_on_time) == "2026-11-03:01:15"
    assert reminder_due(habit, after_time) == "2026-11-03:01:15"


def test_marked_read_reminder_is_not_recreated(client, mongo_client):
    database = mongo_client["Habit-Tracker_test"]
    habit_id = database["habits"].insert_one(
        {
            "name": "Read",
            "frequency": "daily",
            "target": 1,
            "created_at": datetime(2026, 10, 1),
            "user_email": "person@example.com",
            "reminder_enabled": True,
            "reminder_time": "09:30",
            "reminder_timezone": "UTC",
            "completed_dates": [],
        }
    ).inserted_id
    now = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    send_due_reminders(database, now)
    notification = database["notifications"].find_one({"habit_id": habit_id})
    database["notifications"].update_one(
        {"_id": notification["_id"]},
        {"$set": {"read_at": datetime.utcnow()}},
    )

    send_due_reminders(database, now)
    send_due_reminders(database, now)

    assert database["notifications"].count_documents({"habit_id": habit_id}) == 1
    assert database["notifications"].find_one({"habit_id": habit_id}).get("read_at") is not None


def test_invalid_reminder_settings_are_rejected_but_disabled_settings_are_empty():
    errors, _, _ = validate_reminder_settings(
        "weekly", True, "09:30", "UTC", ["funday"], None
    )
    assert "Choose at least one valid weekday for weekly reminders." in errors
    assert validate_reminder_settings("monthly", False, "", "not-a-zone", [], "") == (
        [],
        [],
        None,
    )
