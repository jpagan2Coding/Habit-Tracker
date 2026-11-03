"""Tests for the Mark Habit as Complete feature."""

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
def test_habit(habits):
    """Create a habit owned by the test user."""
    result = habits.insert_one(
        {
            "name": "Drink Water",
            "description": "Drink more water",
            "frequency": "daily",
            "target": 1,
            "user_email": "person@example.com",
            "created_at": datetime.now(timezone.utc),
            "completed_dates": [],
            "status": "active",
        }
    )
    return str(result.inserted_id)


def complete(client, habit_id, token="test-csrf-token"):
    """Send a completion request."""
    return client.post(
        f"/habits/{habit_id}/complete",
        data={"csrf_token": token},
    )


def test_successful_completion(logged_in_client, habits, test_habit):
    response = complete(logged_in_client, test_habit)

    assert response.status_code == 200
    assert response.json["completed"] is True

    habit = habits.find_one({"_id": ObjectId(test_habit)})
    today = datetime.now(timezone.utc).date().isoformat()

    assert today in habit["completed_dates"]


def test_duplicate_completion(logged_in_client, habits, test_habit):
    first = complete(logged_in_client, test_habit)
    second = complete(logged_in_client, test_habit)

    assert first.status_code == 200
    assert second.status_code == 409

    habit = habits.find_one({"_id": ObjectId(test_habit)})
    assert len(habit["completed_dates"]) == 1


def test_logged_out_user_cannot_complete(client, test_habit):
    response = complete(client, test_habit)

    assert response.status_code == 401


def test_cannot_complete_another_users_habit(logged_in_client, habits):
    result = habits.insert_one(
        {
            "name": "Private Habit",
            "user_email": "another@example.com",
            "frequency": "daily",
            "target": 1,
            "completed_dates": [],
        }
    )

    response = complete(logged_in_client, str(result.inserted_id))

    assert response.status_code == 404

    habit = habits.find_one({"_id": result.inserted_id})
    assert habit["completed_dates"] == []


def test_invalid_habit_id(logged_in_client):
    response = complete(logged_in_client, "invalid-id")

    assert response.status_code == 404


@pytest.mark.parametrize("status", ["paused", "archived", "expired"])
def test_inactive_habit_cannot_be_completed(logged_in_client, habits, test_habit, status):
    habits.update_one(
        {"_id": ObjectId(test_habit)},
        {"$set": {"status": status}},
    )

    response = complete(logged_in_client, test_habit)

    assert response.status_code == 409


def test_missing_csrf_token(logged_in_client, test_habit):
    response = logged_in_client.post(f"/habits/{test_habit}/complete")

    assert response.status_code == 400


def test_invalid_csrf_token(logged_in_client, test_habit):
    response = complete(logged_in_client, test_habit, token="wrong-token")

    assert response.status_code == 400


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_target_already_reached(logged_in_client, habits, test_habit, frequency):
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    # For weekly tests, ensure yesterday is within this week.
    if frequency == "weekly" and today.weekday() == 0:
        pytest.skip("Yesterday belongs to the previous week")

    habits.update_one(
        {"_id": ObjectId(test_habit)},
        {
            "$set": {
                "frequency": frequency,
                "target": 1,
                "completed_dates": [yesterday.isoformat()],
            }
        },
    )

    response = complete(logged_in_client, test_habit)

    # Weekly/monthly target already reached.
    # No new completion should be added.
    if frequency == "monthly" and today.day == 1:
        pytest.skip("Yesterday belongs to the previous month")

    assert response.status_code == 409

    habit = habits.find_one({"_id": ObjectId(test_habit)})
    assert len(habit["completed_dates"]) == 1


def test_dashboard_updates_after_completion(logged_in_client, habits, test_habit):
    """Verify the dashboard reflects a completed habit."""

    # Check the dashboard before completion
    before = logged_in_client.get("/dashboard")
    assert before.status_code == 200

    before_html = before.get_data(as_text=True)
    assert "Drink Water" in before_html
    assert "Mark Complete" in before_html

    # Complete the habit through the API
    response = complete(logged_in_client, test_habit)
    assert response.status_code == 200
    assert response.json["completed"] is True

    # Reload the dashboard
    after = logged_in_client.get("/dashboard")
    assert after.status_code == 200

    after_html = after.get_data(as_text=True)

    # Verify the completion is reflected in the UI
    assert "Drink Water" in after_html
    assert "Completed" in after_html
    assert "line-through" in after_html

    # The complete button should no longer appear
    assert "Mark Complete" not in after_html

    # Confirm the completion is saved
    habit = habits.find_one({"_id": ObjectId(test_habit)})
    today = datetime.now(timezone.utc).date().isoformat()

    assert today in habit["completed_dates"]


def test_completing_skipped_habit_replaces_skip(logged_in_client, habits, test_habit):
    today = datetime.now(timezone.utc).date().isoformat()
    habits.update_one({"_id": ObjectId(test_habit)}, {"$set": {"skipped_dates": [today]}})

    before_html = logged_in_client.get("/dashboard").get_data(as_text=True)
    assert "Mark Complete" in before_html

    response = complete(logged_in_client, test_habit)

    assert response.status_code == 200
    habit = habits.find_one({"_id": ObjectId(test_habit)})
    assert habit["completed_dates"] == [today]
    assert habit["skipped_dates"] == []


def test_completing_skipped_habit_keeps_progress_at_most_100(logged_in_client, habits, test_habit):
    today = datetime.now(timezone.utc).date().isoformat()
    habits.insert_one(
        {
            "name": "Stretch",
            "frequency": "daily",
            "target": 1,
            "user_email": "person@example.com",
            "created_at": datetime.now(timezone.utc),
            "completed_dates": [],
            "skipped_dates": [today],
        }
    )
    stretch = habits.find_one({"name": "Stretch"})["_id"]

    complete(logged_in_client, test_habit)
    complete(logged_in_client, str(stretch))

    html = logged_in_client.get("/dashboard").get_data(as_text=True)
    assert "Out of 2 · 100%" in html
    assert "skipped" not in html.split("Out of 2")[1].split("</p>")[0]
