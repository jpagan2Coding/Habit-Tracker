from datetime import datetime, timezone

from bson import ObjectId

from app import DEV_CLOCK_SESSION_KEY


def enable_dev_clock(app, monkeypatch, enabled=True):
    monkeypatch.setitem(app.config, "DEV_TIME_TRAVEL_ENABLED", enabled)
    monkeypatch.setitem(app.config, "DEBUG", False)


def test_settings_shows_developer_clock_only_when_enabled(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch)

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"Developer clock" in response.data
    assert b"simulated_date" in response.data


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

    simulated_date = "2001-02-03"
    response = authenticated_client.post(
        "/settings/dev-clock",
        data={"csrf_token": token, "simulated_date": simulated_date},
    )

    assert response.status_code == 302
    assert b"Today: 2001-02-03" in authenticated_client.get("/dashboard").data

    response = authenticated_client.post(
        f"/habits/{result.inserted_id}/complete",
        data={"csrf_token": token},
    )

    assert response.status_code == 200
    assert response.json["completed_date"] == simulated_date
    habit = habits.find_one({"_id": ObjectId(result.inserted_id)})
    assert habit["completed_dates"] == [simulated_date]


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
    assert f'value="{actual_date}"' in client.get("/settings").get_data(as_text=True)


def test_developer_clock_is_unavailable_when_disabled(client, app, monkeypatch):
    enable_dev_clock(app, monkeypatch, enabled=False)

    response = client.post("/settings/dev-clock")

    assert response.status_code == 404
    assert b"Developer clock" not in client.get("/settings").data
