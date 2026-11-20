"""Acceptance tests for US19: View Habit Completion History."""

from datetime import date, datetime, timezone

import pytest
from bson import ObjectId


@pytest.fixture
def history_client(authenticated_client, monkeypatch):
    """Authenticated client with a consistent application date."""
    monkeypatch.setattr(
        "app.current_app_date",
        lambda: date(2026, 11, 19),
    )
    return authenticated_client


@pytest.fixture
def history_habit(habits):
    """Habit with completed, partial, and skipped history."""
    result = habits.insert_one({
        "name": "Brush Teeth",
        "description": "Morning and evening",
        "frequency": "daily",
        "target": 2,
        "user_email": "person@example.com",
        "created_at": datetime(
            2026, 11, 16, tzinfo=timezone.utc
        ),
        "start_date": "2026-11-16",
        "status": "active",
        "completed_dates": [
            "2026-11-16",
            "2026-11-18",
        ],
        "skipped_dates": ["2026-11-17"],
        "completion_history": [
            {
                "date": "2026-11-16",
                "status": "completed",
                "count": 2,
            },
            {
                "date": "2026-11-18",
                "status": "completed",
                "count": 1,
            },
        ],
    })

    return str(result.inserted_id)


def test_history_page_loads(history_client, history_habit):
    response = history_client.get(
        f"/habits/{history_habit}/history"
    )

    assert response.status_code == 200

    html = response.get_data(as_text=True)

    assert "Completion History" in html
    assert "Brush Teeth" in html


def test_history_displays_completion_statuses(
    history_client, history_habit
):
    response = history_client.get(
        f"/habits/{history_habit}/history"
    )

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Completed" in html
    assert "Missed" in html
    assert "1 skipped" in html
    assert "2 / 2 completed" in html
    assert "1 / 2 completed" in html

def test_current_partial_habit_shows_in_progress(
    history_client, habits, history_habit
):
    habits.update_one(
        {"_id": ObjectId(history_habit)},
        {
            "$addToSet": {
                "completed_dates": "2026-11-19",
            },
            "$push": {
                "completion_history": {
                    "date": "2026-11-19",
                    "status": "completed",
                    "count": 1,
                }
            },
        },
    )

    response = history_client.get(
        f"/habits/{history_habit}/history"
    )

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "In progress" in html
    assert "Nov 19, 2026" in html
    assert "1 / 2 completed" in html

def test_history_is_newest_first(
    history_client, history_habit
):
    response = history_client.get(
        f"/habits/{history_habit}/history"
    )

    html = response.get_data(as_text=True)

    assert html.index("Nov 18, 2026") < html.index("Nov 17, 2026")
    assert html.index("Nov 17, 2026") < html.index("Nov 16, 2026")


def test_history_date_filter(history_client, history_habit):
    response = history_client.get(
        f"/habits/{history_habit}/history?period=7"
    )

    assert response.status_code == 200
    assert "Brush Teeth" in response.get_data(as_text=True)


def test_history_empty_state(history_client, habits):
    result = habits.insert_one({
        "name": "New Habit",
        "frequency": "daily",
        "target": 1,
        "user_email": "person@example.com",
        "created_at": datetime(
            2026, 11, 19, tzinfo=timezone.utc
        ),
        "start_date": "2026-11-19",
        "completed_dates": [],
        "skipped_dates": [],
    })

    response = history_client.get(
        f"/habits/{result.inserted_id}/history"
    )

    assert response.status_code == 200
    assert b"No completion history available yet" in response.data


def test_history_requires_login(client, history_habit):
    response = client.get(
        f"/habits/{history_habit}/history"
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_cannot_view_another_users_history(
    history_client, habits
):
    result = habits.insert_one({
        "name": "Private Habit",
        "frequency": "daily",
        "target": 1,
        "user_email": "another@example.com",
        "completed_dates": ["2026-11-18"],
    })

    response = history_client.get(
        f"/habits/{result.inserted_id}/history"
    )

    assert response.status_code == 404
    assert b"Private Habit" not in response.data


def test_invalid_habit_id(history_client):
    response = history_client.get(
        "/habits/invalid-id/history"
    )

    assert response.status_code == 404


def test_nonexistent_habit(history_client):
    response = history_client.get(
        f"/habits/{ObjectId()}/history"
    )

    assert response.status_code == 404
