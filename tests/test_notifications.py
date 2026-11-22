from datetime import datetime, timezone
from pathlib import Path

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
    assert notification["reminder_time"] == "09:30"
    assert notification["reminder_date"] == "2026-11-03"

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


def test_mark_read_removes_today_reminder_highlight(client, mongo_client):
    _login(client)
    database = mongo_client["Habit-Tracker_test"]
    today = datetime.now(timezone.utc).date().isoformat()
    habit_id = database["habits"].insert_one(
        {
            "name": "Reading",
            "frequency": "daily",
            "target": 1,
            "user_email": OWNER_EMAIL,
            "created_at": datetime.now(timezone.utc),
            "completed_dates": [],
            "reminder_enabled": True,
            "reminder_time": "09:30",
            "reminder_timezone": "UTC",
        }
    ).inserted_id
    notification_id = database["notifications"].insert_one(
        {
            "user_email": OWNER_EMAIL,
            "habit_id": habit_id,
            "habit_name": "Reading",
            "message": "Reminder",
            "occurrence_key": f"{today}:09:30",
            "created_at": datetime.now(timezone.utc),
        }
    ).inserted_id

    client.get("/dashboard")
    with client.session_transaction() as session:
        token = session["habit_form_token"]
    client.post(
        f"/api/notifications/{notification_id}/read",
        headers={"X-CSRF-Token": token},
    )

    response = client.get("/dashboard")
    html = response.get_data(as_text=True)
    assert "reminder-highlight" not in html
    assert "Reminder due today" not in html


@pytest.mark.parametrize("action", ["skip", "pause", "complete"])
def test_habit_action_clears_pending_reminder(
    client, mongo_client, habits, action
):
    _login(client)
    today = datetime.now(timezone.utc).date().isoformat()
    habit_id = habits.insert_one(
        {
            "name": "Reading",
            "frequency": "daily",
            "target": 1,
            "user_email": OWNER_EMAIL,
            "created_at": datetime.now(timezone.utc),
            "completed_dates": [],
            "skipped_dates": [],
            "reminder_enabled": True,
            "reminder_time": "09:30",
            "reminder_timezone": "UTC",
        }
    ).inserted_id
    database = mongo_client["Habit-Tracker_test"]
    notification_id = database["notifications"].insert_one(
        {
            "user_email": OWNER_EMAIL,
            "habit_id": habit_id,
            "habit_name": "Reading",
            "message": "Reminder",
            "occurrence_key": f"{today}:09:30",
            "created_at": datetime.now(timezone.utc),
        }
    ).inserted_id

    client.get("/dashboard")
    with client.session_transaction() as session:
        token = session["habit_form_token"]

    data = {"csrf_token": token}
    if action == "pause":
        data["days"] = "1"
    response = client.post(f"/habits/{habit_id}/{action}", data=data)
    assert response.status_code in (200, 302)

    notification = database["notifications"].find_one({"_id": notification_id})
    assert notification.get("read_at") is not None

    response = client.get("/dashboard")
    html = response.get_data(as_text=True)
    assert "reminder-highlight" not in html
    assert "Reminder due today" not in html


@pytest.mark.parametrize("notification_id", ["not-an-object-id", str(ObjectId())])
def test_notification_read_returns_not_found_for_invalid_or_missing_id(client, notification_id):
    _login(client)
    client.get("/dashboard")
    with client.session_transaction() as session:
        token = session["habit_form_token"]

    response = client.post(
        f"/api/notifications/{notification_id}/read",
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 404


def test_push_notification_does_not_navigate_when_clicked():
    service_worker = (
        Path(__file__).resolve().parents[1] / "static" / "push-service-worker.js"
    ).read_text(encoding="utf-8")

    assert "notificationclick" not in service_worker
    assert "clients.openWindow" not in service_worker
