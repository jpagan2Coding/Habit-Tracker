from datetime import datetime, timezone

import pytest
from bson import ObjectId

from reminders import send_due_reminders

OWNER_EMAIL = "person@example.com"


def _login(client, email=OWNER_EMAIL):
    with client.session_transaction() as session:
        session["user_id"] = f"user-{email}"
        session["user_email"] = email


def test_due_reminder_creates_persistent_notification_without_push_config(client, mongo_client):
    _login(client)
    database = mongo_client["Habit-Tracker_test"]
    habit_id = (
        database["habits"]
        .insert_one(
            {
                "name": "Read",
                "frequency": "daily",
                "target": 1,
                "created_at": datetime(2026, 10, 1),
                "user_email": OWNER_EMAIL,
                "reminder_enabled": True,
                "reminder_time": "09:30",
                "reminder_timezone": "UTC",
            }
        )
        .inserted_id
    )

    now = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    assert send_due_reminders(database, now) == 0
    notification = database["notifications"].find_one({"habit_id": habit_id})
    assert notification["habit_name"] == "Read"
    assert notification["message"] == "Reminder: Read is scheduled now."
    assert notification["user_email"] == OWNER_EMAIL

    send_due_reminders(database, now)
    assert database["notifications"].count_documents({"habit_id": habit_id}) == 1


def test_dashboard_shows_only_current_users_notifications(client, mongo_client):
    _login(client)
    notifications = mongo_client["Habit-Tracker_test"]["notifications"]
    notifications.insert_many(
        [
            {
                "user_email": OWNER_EMAIL,
                "habit_name": "Mine",
                "message": "My reminder",
                "url": "/dashboard",
                "created_at": datetime.utcnow(),
            },
            {
                "user_email": "other@example.com",
                "habit_name": "Other",
                "message": "Other reminder",
                "url": "/dashboard",
                "created_at": datetime.utcnow(),
            },
        ]
    )

    response = client.get("/dashboard")
    assert b"My reminder" in response.data
    assert b"Other reminder" not in response.data
    assert b'aria-label="1 unread notifications"' in response.data


def test_notification_read_endpoints_are_user_scoped(client, mongo_client):
    _login(client)
    notifications = mongo_client["Habit-Tracker_test"]["notifications"]
    mine = notifications.insert_one(
        {
            "user_email": OWNER_EMAIL,
            "habit_name": "Mine",
            "message": "Reminder",
            "created_at": datetime.utcnow(),
        }
    ).inserted_id
    other = notifications.insert_one(
        {"user_email": "other@example.com", "habit_name": "Other"}
    ).inserted_id
    client.get("/dashboard")
    with client.session_transaction() as session:
        token = session["habit_form_token"]

    response = client.post(
        f"/api/notifications/{mine}/read",
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    assert notifications.find_one({"_id": mine}).get("read_at")

    response = client.post(
        f"/api/notifications/{other}/read",
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 404
    assert notifications.find_one({"_id": other}).get("read_at") is None


@pytest.mark.parametrize("notification_id", ["not-an-object-id", str(ObjectId())])
def test_notification_read_returns_not_found_for_invalid_or_missing_id(
    client, notification_id
):
    _login(client)
    client.get("/dashboard")
    with client.session_transaction() as session:
        token = session["habit_form_token"]

    response = client.post(
        f"/api/notifications/{notification_id}/read",
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 404
