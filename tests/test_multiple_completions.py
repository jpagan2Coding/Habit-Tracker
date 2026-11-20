"""Tests for count-based habit completion."""

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from progress import current_period_progress


@pytest.fixture
def multi_habit(habits):
    result = habits.insert_one({
        "name": "Brush Teeth",
        "frequency": "daily",
        "target": 2,
        "user_email": "person@example.com",
        "created_at": datetime.now(timezone.utc),
        "completed_dates": [],
        "skipped_dates": [],
        "status": "active",
    })
    return str(result.inserted_id)


@pytest.fixture
def logged_in_client(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user-id"
        sess["user_email"] = "person@example.com"
        sess["habit_form_token"] = "test-token"
    return client


def post_action(client, habit_id, action):
    return client.post(
        f"/habits/{habit_id}/{action}",
        data={"csrf_token": "test-token"},
    )


@pytest.mark.parametrize("frequency", ["daily", "weekly", "monthly"])
def test_multiple_checkins(logged_in_client, habits, multi_habit, frequency):
    habits.update_one(
        {"_id": ObjectId(multi_habit)},
        {"$set": {"frequency": frequency}},
    )

    first = post_action(logged_in_client, multi_habit, "complete")
    second = post_action(logged_in_client, multi_habit, "complete")
    third = post_action(logged_in_client, multi_habit, "complete")

    assert first.status_code == 200
    assert first.json["completed"] is False

    assert second.status_code == 200
    assert second.json["completed"] is True

    assert third.status_code == 409

    habit = habits.find_one({"_id": ObjectId(multi_habit)})
    today = datetime.now(timezone.utc).date()

    progress = current_period_progress(habit, today)
    assert progress["completed"] == 2
    assert progress["status"] == "Complete"


@pytest.mark.parametrize("frequency", ["daily", "weekly", "monthly"])
def test_undo_removes_one_checkin(
    logged_in_client, habits, multi_habit, frequency
):
    habits.update_one(
        {"_id": ObjectId(multi_habit)},
        {"$set": {"frequency": frequency}},
    )

    post_action(logged_in_client, multi_habit, "complete")
    post_action(logged_in_client, multi_habit, "complete")

    response = post_action(logged_in_client, multi_habit, "uncomplete")

    assert response.status_code == 200
    assert response.json["count"] == 1

    habit = habits.find_one({"_id": ObjectId(multi_habit)})
    today = datetime.now(timezone.utc).date()

    progress = current_period_progress(habit, today)
    assert progress["completed"] == 1
    assert progress["status"] == "In Progress"


def test_dashboard_shows_in_progress(
    logged_in_client, habits, multi_habit
):
    post_action(logged_in_client, multi_habit, "complete")

    response = logged_in_client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "In Progress" in html
    assert "1/2" in html
    assert "Mark Complete" in html
    assert "Undo" in html
