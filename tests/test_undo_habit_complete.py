"""Tests for the Undo Habit Completion feature."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId


@pytest.fixture
def logged_in_client(client):
    """Simulate a logged-in user with a valid CSRF token."""
    with client.session_transaction() as sess:
        sess["user_id"] = str(ObjectId())
        sess["user_email"] = "person@example.com"
        sess["username"] = "Test User"
        sess["habit_form_token"] = "test-csrf-token"

    return client


@pytest.fixture
def today():
    return datetime.now(timezone.utc).date().isoformat()


@pytest.fixture
def completed_habit(habits, today):
    """Create a habit that has been completed today."""
    result = habits.insert_one(
        {
            "name": "Drink Water",
            "description": "Drink more water",
            "frequency": "daily",
            "target": 1,
            "user_email": "person@example.com",
            "created_at": datetime.now(timezone.utc),
            "completed_dates": [today],
            "skipped_dates": [],
            "status": "active",
        }
    )
    return str(result.inserted_id)


def undo(client, habit_id, token="test-csrf-token"):
    """Send an undo completion request."""
    return client.post(
        f"/habits/{habit_id}/uncomplete",
        data={"csrf_token": token},
    )


def complete(client, habit_id):
    """Send a completion request."""
    return client.post(
        f"/habits/{habit_id}/complete",
        data={"csrf_token": "test-csrf-token"},
    )


def test_successful_undo(logged_in_client, habits, completed_habit):
    response = undo(logged_in_client, completed_habit)

    assert response.status_code == 200
    assert response.json["completed"] is False

    habit = habits.find_one({"_id": ObjectId(completed_habit)})
    assert habit["completed_dates"] == []
    assert habit["status"] == "active"


def test_undo_preserves_previous_history(logged_in_client, habits, completed_habit, today):
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    habits.update_one(
        {"_id": ObjectId(completed_habit)},
        {"$set": {"completed_dates": [yesterday, today]}},
    )

    response = undo(logged_in_client, completed_habit)

    assert response.status_code == 200

    habit = habits.find_one({"_id": ObjectId(completed_habit)})
    assert habit["completed_dates"] == [yesterday]


def test_duplicate_undo(logged_in_client, habits, completed_habit):
    first = undo(logged_in_client, completed_habit)
    second = undo(logged_in_client, completed_habit)

    assert first.status_code == 200
    assert second.status_code == 409

    habit = habits.find_one({"_id": ObjectId(completed_habit)})
    assert habit["completed_dates"] == []


def test_logged_out_user_cannot_undo(client, completed_habit):
    response = undo(client, completed_habit)

    assert response.status_code == 401


def test_cannot_undo_another_users_habit(logged_in_client, habits, today):
    result = habits.insert_one(
        {
            "name": "Private Habit",
            "user_email": "another@example.com",
            "frequency": "daily",
            "target": 1,
            "completed_dates": [today],
        }
    )

    response = undo(logged_in_client, str(result.inserted_id))

    assert response.status_code == 404

    habit = habits.find_one({"_id": result.inserted_id})
    assert habit["completed_dates"] == [today]


def test_invalid_habit_id(logged_in_client):
    response = undo(logged_in_client, "invalid-id")

    assert response.status_code == 404


def test_nonexistent_habit(logged_in_client):
    response = undo(logged_in_client, str(ObjectId()))

    assert response.status_code == 404


def test_missing_csrf_token(logged_in_client, completed_habit):
    response = logged_in_client.post(f"/habits/{completed_habit}/uncomplete")

    assert response.status_code == 400


def test_invalid_csrf_token(logged_in_client, completed_habit):
    response = undo(logged_in_client, completed_habit, token="wrong-token")

    assert response.status_code == 400


def test_can_complete_again_after_undo(logged_in_client, habits, completed_habit, today):
    undo_response = undo(logged_in_client, completed_habit)
    complete_response = complete(logged_in_client, completed_habit)

    assert undo_response.status_code == 200
    assert complete_response.status_code == 200

    habit = habits.find_one({"_id": ObjectId(completed_habit)})
    assert habit["completed_dates"] == [today]


def test_dashboard_updates_after_undo(logged_in_client, completed_habit):
    before = logged_in_client.get("/dashboard").get_data(as_text=True)

    assert f"/habits/{completed_habit}/uncomplete" in before
    assert "line-through" in before

    response = undo(logged_in_client, completed_habit)

    assert response.status_code == 200

    after = logged_in_client.get("/dashboard").get_data(as_text=True)

    assert f"/habits/{completed_habit}/complete" in after
    assert f"/habits/{completed_habit}/uncomplete" not in after
    assert "Mark Complete" in after

    todays_habits = after.split("<!-- Today's Habits -->", 1)[1]
    todays_habits = todays_habits.split("<!-- All My Habits -->", 1)[0]

    assert "line-through" not in todays_habits


def test_all_habits_status_updates(logged_in_client, completed_habit):
    before = logged_in_client.get("/dashboard").get_data(as_text=True)
    before_table = before.split("<!-- All My Habits -->", 1)[1]

    assert ">Complete<" in "".join(before_table.split())

    assert undo(logged_in_client, completed_habit).status_code == 200

    after = logged_in_client.get("/dashboard").get_data(as_text=True)
    after_table = after.split("<!-- All My Habits -->", 1)[1]

    assert ">Active<" in "".join(after_table.split())
    assert ">Complete<" not in "".join(after_table.split())


def test_daily_progress_updates_after_undo(logged_in_client, completed_habit):
    before = logged_in_client.get("/dashboard").get_data(as_text=True)
    assert "Out of 1 · 100%" in before

    assert undo(logged_in_client, completed_habit).status_code == 200

    after = logged_in_client.get("/dashboard").get_data(as_text=True)
    assert "Out of 1 · 0%" in after


def test_undo_does_not_create_skip(logged_in_client, habits, completed_habit):
    response = undo(logged_in_client, completed_habit)

    assert response.status_code == 200

    habit = habits.find_one({"_id": ObjectId(completed_habit)})
    assert habit["completed_dates"] == []
    assert habit["skipped_dates"] == []


@pytest.mark.parametrize("frequency", ["daily", "weekly", "monthly"])
def test_undo_supported_frequencies(logged_in_client, habits, completed_habit, frequency):
    habits.update_one(
        {"_id": ObjectId(completed_habit)},
        {"$set": {"frequency": frequency}},
    )

    response = undo(logged_in_client, completed_habit)

    assert response.status_code == 200

    habit = habits.find_one({"_id": ObjectId(completed_habit)})
    assert habit["completed_dates"] == []
    assert habit["frequency"] == frequency
