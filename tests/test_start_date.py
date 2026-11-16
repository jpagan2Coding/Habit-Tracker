from datetime import date, datetime, timedelta, timezone

import pytest

from app import is_due_today
from progress import calculate_overall_progress, is_scheduled_on
from reminders import reminder_due

OWNER_EMAIL = "person@example.com"

VALID_HABIT = {
    "name": "Morning stretch",
    "description": "",
    "target": "1",
    "frequency": "daily",
}


def _days_from_today(days):
    today = datetime.utcnow().date()
    return (today + timedelta(days=days)).isoformat()


def _add_habit(habits, name, start_date=None):
    habit = {
        "name": name,
        "target": 1,
        "frequency": "daily",
        "user_email": OWNER_EMAIL,
        "created_at": datetime.utcnow(),
        "completed_dates": [],
    }
    if start_date:
        habit["start_date"] = start_date
    return habits.insert_one(habit).inserted_id


def _edit_habit(client, habit_id, **changes):
    client.get(f"/habits/{habit_id}/edit")
    with client.session_transaction() as session:
        token = session["habit_form_token"]

    form = {**VALID_HABIT, "csrf_token": token, **changes}
    return client.post(f"/habits/{habit_id}/edit", data=form)


# choosing a start date


def test_create_form_offers_start_date_defaulting_to_today(authenticated_client):
    html = authenticated_client.get("/habit-tracker").get_data(as_text=True)

    assert 'name="start_date"' in html
    assert f'value="{_days_from_today(0)}"' in html


def test_create_habit_saves_future_start_date(authenticated_client, habits):
    start = _days_from_today(5)

    response = authenticated_client.post(
        "/habit-tracker", data={**VALID_HABIT, "start_date": start}
    )

    assert response.status_code == 302
    assert habits.find_one({"name": "Morning stretch"})["start_date"] == start


def test_blank_start_date_starts_today(authenticated_client, habits):
    authenticated_client.post("/habit-tracker", data={**VALID_HABIT, "start_date": ""})

    assert habits.find_one({"name": "Morning stretch"})["start_date"] == _days_from_today(0)


@pytest.mark.parametrize(
    ("start_date", "message"),
    [
        (_days_from_today(-1), "Start date cannot be in the past."),
        ("not-a-date", "Enter a valid start date."),
    ],
)
def test_invalid_start_date_is_rejected(authenticated_client, habits, start_date, message):
    response = authenticated_client.post(
        "/habit-tracker", data={**VALID_HABIT, "start_date": start_date}
    )

    assert response.status_code == 400
    assert message.encode() in response.data
    assert habits.count_documents({}) == 0


# habits don't appear before their start date


def test_habit_is_not_due_before_its_start_date():
    habit = {"frequency": "daily", "target": 1, "start_date": "2026-11-20"}

    assert not is_due_today(habit, date(2026, 11, 19))
    assert is_due_today(habit, date(2026, 11, 20))


def test_habit_without_start_date_is_still_due():
    habit = {"frequency": "daily", "target": 1}

    assert is_due_today(habit, date(2026, 11, 19))


def test_upcoming_habit_is_hidden_from_today_but_listed_in_all_habits(authenticated_client, habits):
    start = _days_from_today(3)
    _add_habit(habits, "Future habit", start_date=start)
    _add_habit(habits, "Current habit")

    html = authenticated_client.get("/dashboard").get_data(as_text=True)
    todays = html[html.index("Today's Habits") : html.index("All My Habits")]
    all_habits = html[html.index("All My Habits") :]

    assert "Current habit" in todays
    assert "Future habit" not in todays
    assert "Future habit" in all_habits
    assert "Upcoming" in all_habits
    assert date.fromisoformat(start).strftime("%B %d, %Y") in all_habits


def test_habit_cannot_be_completed_before_its_start_date(authenticated_client, habits):
    habit_id = _add_habit(habits, "Future habit", start_date=_days_from_today(3))
    with authenticated_client.session_transaction() as session:
        session["habit_form_token"] = "test-csrf-token"

    response = authenticated_client.post(
        f"/habits/{habit_id}/complete", data={"csrf_token": "test-csrf-token"}
    )

    assert response.status_code == 409
    assert habits.find_one({"_id": habit_id})["completed_dates"] == []


def test_no_reminder_or_progress_before_start_date():
    habit = {
        "frequency": "daily",
        "target": 1,
        "created_at": datetime(2026, 11, 1),
        "start_date": "2026-11-10",
        "completed_dates": ["2026-11-10"],
        "reminder_time": "08:00",
        "reminder_timezone": "UTC",
    }

    assert reminder_due(habit, datetime(2026, 11, 9, 8, tzinfo=timezone.utc)) is None
    assert reminder_due(habit, datetime(2026, 11, 10, 8, tzinfo=timezone.utc)) == "2026-11-10:08:00"

    progress = calculate_overall_progress([habit], end_date=date(2026, 11, 10), period_days=7)
    assert progress["scheduled"] == 1
    assert progress["percentage"] == 100


def test_weekly_schedule_repeats_from_start_date():
    # created on a Sunday, set to start on a Thursday
    habit = {
        "frequency": "weekly",
        "target": 1,
        "created_at": datetime(2026, 11, 1),
        "start_date": "2026-11-12",
    }

    assert is_scheduled_on(habit, date(2026, 11, 19))
    assert not is_scheduled_on(habit, date(2026, 11, 15))


# editing the start date


def test_edit_keeps_start_date_that_has_already_passed(authenticated_client, habits):
    habit_id = _add_habit(habits, "Old habit", start_date="2026-01-05")

    response = _edit_habit(
        authenticated_client, habit_id, name="Renamed habit", start_date="2026-01-05"
    )

    assert response.status_code == 302
    saved = habits.find_one({"_id": habit_id})
    assert saved["name"] == "Renamed habit"
    assert saved["start_date"] == "2026-01-05"


def test_edit_can_move_start_date_later(authenticated_client, habits):
    habit_id = _add_habit(habits, "Future habit", start_date=_days_from_today(3))
    later = _days_from_today(10)

    response = _edit_habit(authenticated_client, habit_id, start_date=later)

    assert response.status_code == 302
    assert habits.find_one({"_id": habit_id})["start_date"] == later


def test_edit_cannot_move_start_date_into_the_past(authenticated_client, habits):
    original = _days_from_today(3)
    habit_id = _add_habit(habits, "Future habit", start_date=original)

    response = _edit_habit(authenticated_client, habit_id, start_date=_days_from_today(-2))

    assert response.status_code == 400
    assert b"Start date cannot be in the past." in response.data
    assert habits.find_one({"_id": habit_id})["start_date"] == original