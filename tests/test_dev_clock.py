from datetime import datetime, timezone

from bson import ObjectId

from app import DEV_CLOCK_SESSION_KEY, current_app_datetime


def enable_dev_clock(app, monkeypatch, enabled=True):
    monkeypatch.setitem(app.config, "DEV_TIME_TRAVEL_ENABLED", enabled)
    monkeypatch.setitem(app.config, "DEBUG", False)


def test_settings_shows_developer_clock_only_when_enabled(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch)

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"Developer clock" in response.data
    assert b"simulated_datetime" in response.data


def test_developer_clock_changes_dashboard_and_completion_date(
    authenticated_client, app, monkeypatch, habits
):
    enable_dev_clock(app, monkeypatch)
    result = habits.insert_one(
        {
            "name": "Test habit",
            "frequency": "daily",
            "target": 1,
            "user_email": "person@example.com",
            "created_at": datetime.now(timezone.utc),
            "completed_dates": [],
        }
    )
    settings_response = authenticated_client.get("/settings")
    assert settings_response.status_code == 200
    with authenticated_client.session_transaction() as session:
        token = session["habit_form_token"]

    simulated_datetime = "2001-02-03T04:05"
    response = authenticated_client.post(
        "/settings/dev-clock",
        data={"csrf_token": token, "simulated_datetime": simulated_datetime},
    )

    assert response.status_code == 302
    with authenticated_client:
        assert b"Today: 2001-02-03" in authenticated_client.get("/dashboard").data
        assert current_app_datetime() == datetime(2001, 2, 3, 4, 5, tzinfo=timezone.utc)

    response = authenticated_client.post(
        f"/habits/{result.inserted_id}/complete",
        data={"csrf_token": token},
    )

    assert response.status_code == 200
    assert response.json["completed_date"] == "2001-02-03"
    habit = habits.find_one({"_id": ObjectId(result.inserted_id)})
    assert habit["completed_dates"] == ["2001-02-03"]


def test_developer_clock_accepts_date_only_sessions(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch)
    client.get("/settings")
    with client.session_transaction() as session:
        session[DEV_CLOCK_SESSION_KEY] = "2001-02-03"

    with client:
        client.get("/settings")
        now = datetime.now(timezone.utc)
        simulated = current_app_datetime()

    assert simulated.date().isoformat() == "2001-02-03"
    assert simulated.hour == now.hour
    assert simulated.minute == now.minute


def test_developer_clock_rejects_invalid_datetime(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch)
    client.get("/settings")
    with client.session_transaction() as session:
        token = session["habit_form_token"]

    response = client.post(
        "/settings/dev-clock",
        data={"csrf_token": token, "simulated_datetime": "not-a-date"},
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert DEV_CLOCK_SESSION_KEY not in session


def test_developer_clock_reset_restores_actual_date(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch)
    client.get("/settings")
    with client.session_transaction() as session:
        session[DEV_CLOCK_SESSION_KEY] = "2001-02-03"
        token = session["habit_form_token"]

    response = client.post(
        "/settings/dev-clock",
        data={"csrf_token": token, "reset": "1"},
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert DEV_CLOCK_SESSION_KEY not in session
    actual_date = datetime.now(timezone.utc).date().isoformat()
    actual_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    assert f'value="{actual_datetime}"' in client.get("/settings").get_data(as_text=True)


def test_developer_clock_is_unavailable_when_disabled(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch, enabled=False)

    response = client.post("/settings/dev-clock")

    assert response.status_code == 404
    assert b"Developer clock" not in client.get("/settings").data
