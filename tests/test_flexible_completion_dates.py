import re
from datetime import date, datetime

import mongomock
import pytest

import app as app_module
from progress import completion_count_on


TODAY = date(2026, 11, 22)


def make_habit(**changes):
    habit = {
        "name": "Read",
        "description": "Read every day",
        "frequency": "daily",
        "target": 1,
        "status": "active",
        "start_date": "2026-11-01",
        "created_at": datetime(2026, 11, 1, 12, 0),
        "completed_dates": [],
        "skipped_dates": [],
        "breaks": [],
    }
    habit.update(changes)
    return habit


# Completion-date validation tests.


@pytest.mark.parametrize(
    "value",
    ["2026-11-01", "2026-11-21", "2026-11-22"],
)
def test_completion_date_accepts_valid_dates(value):
    selected, error = app_module._validate_completion_date(
        value, make_habit(), TODAY
    )

    assert selected == date.fromisoformat(value)
    assert error is None


@pytest.mark.parametrize(
    "value",
    ["", None, "not-a-date", "2026-02-30"],
)
def test_completion_date_rejects_invalid_dates(value):
    selected, error = app_module._validate_completion_date(
        value, make_habit(), TODAY
    )

    assert selected is None
    assert error


def test_completion_date_requires_iso_format():
    selected, error = app_module._validate_completion_date(
        "20261121", make_habit(), TODAY
    )

    assert selected is None
    assert error


def test_completion_date_rejects_future():
    selected, error = app_module._validate_completion_date(
        "2026-11-23", make_habit(), TODAY
    )

    assert selected is None
    assert error


def test_completion_date_rejects_before_start():
    selected, error = app_module._validate_completion_date(
        "2026-10-31", make_habit(), TODAY
    )

    assert selected is None
    assert error


def test_completion_date_uses_creation_date_for_legacy_habit():
    habit = make_habit()
    habit.pop("start_date")

    selected, error = app_module._validate_completion_date(
        "2026-10-31", habit, TODAY
    )

    assert selected is None
    assert error


def test_completion_date_accepts_legacy_creation_day():
    habit = make_habit()
    habit.pop("start_date")

    selected, error = app_module._validate_completion_date(
        "2026-11-01", habit, TODAY
    )

    assert selected == date(2026, 11, 1)
    assert error is None


@pytest.mark.parametrize("status", ["paused", "archived", "expired"])
def test_completion_date_rejects_inactive_habit(status):
    selected, error = app_module._validate_completion_date(
        "2026-11-21",
        make_habit(status=status),
        TODAY,
    )

    assert selected is None
    assert error


@pytest.mark.parametrize(
    "value",
    ["2026-11-10", "2026-11-11", "2026-11-12"],
)
def test_completion_date_rejects_break_including_boundaries(value):
    habit = make_habit(
        breaks=[{"start": "2026-11-10", "end": "2026-11-12"}]
    )

    selected, error = app_module._validate_completion_date(
        value, habit, TODAY
    )

    assert selected is None
    assert error


@pytest.mark.parametrize("value", ["2026-11-09", "2026-11-13"])
def test_completion_date_accepts_dates_outside_break(value):
    habit = make_habit(
        breaks=[{"start": "2026-11-10", "end": "2026-11-12"}]
    )

    selected, error = app_module._validate_completion_date(
        value, habit, TODAY
    )

    assert selected == date.fromisoformat(value)
    assert error is None


def test_completion_date_respects_supplied_app_date():
    supplied_today = date(2026, 11, 15)

    selected, error = app_module._validate_completion_date(
        "2026-11-16", make_habit(), supplied_today
    )

    assert selected is None
    assert error


# Web fixtures and helpers.


@pytest.fixture
def completion_web(monkeypatch):
    database = mongomock.MongoClient()["us38_tests"]

    monkeypatch.setattr(app_module, "get_db", lambda: database)
    monkeypatch.setattr(app_module, "current_app_date", lambda: TODAY)
    monkeypatch.setitem(app_module.app.config, "TESTING", True)

    with app_module.app.test_client() as web:
        with web.session_transaction() as session:
            session["user_id"] = "us38-test-user"
            session["user_email"] = "us38@example.com"
            session["habit_form_token"] = "us38-csrf"

        yield web, database["habits"]


def insert_completion_habit(habits, **changes):
    habit = make_habit(user_email="us38@example.com", **changes)
    return habits.insert_one(habit).inserted_id


def completion_form_data(web, habit_id, selected_date):
    response = web.get(f"/habits/{habit_id}/record-completion")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    match = re.search(
        r'name="submission_token"\s+value="([^"]+)"',
        html,
    )
    assert match is not None

    return {
        "csrf_token": "us38-csrf",
        "submission_token": match.group(1),
        "completion_date": selected_date,
    }


# Completion form and route tests.


def test_record_form_defaults_to_app_date_without_writing(completion_web):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits)
    before = habits.find_one({"_id": habit_id})

    response = web.get(f"/habits/{habit_id}/record-completion")

    assert response.status_code == 200
    assert b'value="2026-11-22"' in response.data
    assert habits.find_one({"_id": habit_id}) == before


@pytest.mark.parametrize("selected", ["2026-11-21", "2026-11-22"])
def test_record_route_saves_selected_date(completion_web, selected):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits)
    data = completion_form_data(web, habit_id, selected)

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/habits/{habit_id}/history"
    )

    saved = habits.find_one({"_id": habit_id})
    assert completion_count_on(saved, date.fromisoformat(selected)) == 1
    assert selected in saved["completed_dates"]
    assert len(saved["completion_submission_ids"]) == 1

    history = web.get(response.headers["Location"])
    assert history.status_code == 200
    assert b"Completed" in history.data


@pytest.mark.parametrize(
    "selected",
    ["", "not-a-date", "2026-11-23", "2026-10-31"],
)
def test_record_route_rejects_invalid_dates_without_writing(
    completion_web, selected
):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits)
    data = completion_form_data(web, habit_id, selected)
    before = habits.find_one({"_id": habit_id})

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == before


def test_record_route_rejects_break_date(completion_web):
    web, habits = completion_web
    habit_id = insert_completion_habit(
        habits,
        breaks=[{"start": "2026-11-20", "end": "2026-11-21"}],
    )
    data = completion_form_data(web, habit_id, "2026-11-21")
    before = habits.find_one({"_id": habit_id})

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == before


def test_same_form_cannot_record_twice(completion_web):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits, target=3)
    data = completion_form_data(web, habit_id, "2026-11-21")

    first = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )
    assert first.status_code == 302

    after_first = habits.find_one({"_id": habit_id})

    second = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert second.status_code == 302
    assert habits.find_one({"_id": habit_id}) == after_first
    assert completion_count_on(after_first, date(2026, 11, 21)) == 1


def test_new_form_allows_another_intentional_checkin(completion_web):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits, target=3)

    for _ in range(2):
        data = completion_form_data(web, habit_id, "2026-11-21")
        response = web.post(
            f"/habits/{habit_id}/record-completion",
            data=data,
        )
        assert response.status_code == 302

    saved = habits.find_one({"_id": habit_id})
    assert completion_count_on(saved, date(2026, 11, 21)) == 2


@pytest.mark.parametrize("frequency", ["daily", "weekly", "monthly"])
def test_record_route_rejects_full_period(completion_web, frequency):
    web, habits = completion_web
    habit_id = insert_completion_habit(
        habits,
        frequency=frequency,
        target=1,
        completed_dates=["2026-11-21"],
    )
    selected = "2026-11-21" if frequency == "daily" else "2026-11-20"
    data = completion_form_data(web, habit_id, selected)
    before = habits.find_one({"_id": habit_id})

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 409
    assert habits.find_one({"_id": habit_id}) == before


def test_record_route_preserves_history_and_replaces_skip(completion_web):
    web, habits = completion_web
    earlier_record = {
        "date": "2026-11-19",
        "status": "completed",
        "count": 1,
    }
    habit_id = insert_completion_habit(
        habits,
        completed_dates=["2026-11-19"],
        skipped_dates=["2026-11-21"],
        completion_history=[
            earlier_record,
            {
                "date": "2026-11-21",
                "status": "skipped",
                "count": 0,
            },
        ],
    )
    data = completion_form_data(web, habit_id, "2026-11-21")

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 302
    saved = habits.find_one({"_id": habit_id})

    assert earlier_record in saved["completion_history"]
    assert "2026-11-19" in saved["completed_dates"]
    assert "2026-11-21" not in saved["skipped_dates"]
    assert completion_count_on(saved, date(2026, 11, 21)) == 1


@pytest.mark.parametrize("csrf", [None, "incorrect"])
def test_record_route_requires_csrf(completion_web, csrf):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits)
    data = completion_form_data(web, habit_id, "2026-11-21")

    if csrf is None:
        data.pop("csrf_token")
    else:
        data["csrf_token"] = csrf

    before = habits.find_one({"_id": habit_id})

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == before


def test_record_route_rejects_tampered_submission_token(completion_web):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits)
    data = completion_form_data(web, habit_id, "2026-11-21")
    data["submission_token"] = "invalid"
    before = habits.find_one({"_id": habit_id})

    response = web.post(
        f"/habits/{habit_id}/record-completion",
        data=data,
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == before


@pytest.mark.parametrize("method", ["get", "post"])
def test_record_route_requires_login(completion_web, method):
    web, habits = completion_web
    habit_id = insert_completion_habit(habits)

    with web.session_transaction() as session:
        session.clear()

    response = getattr(web, method)(
        f"/habits/{habit_id}/record-completion"
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_record_route_rejects_other_users_habit(completion_web, method):
    web, habits = completion_web
    habit = make_habit(user_email="someone-else@example.com")
    habit_id = habits.insert_one(habit).inserted_id
    before = habits.find_one({"_id": habit_id})

    response = getattr(web, method)(
        f"/habits/{habit_id}/record-completion",
        data={"csrf_token": "us38-csrf"},
    )

    assert response.status_code == 404
    assert habits.find_one({"_id": habit_id}) == before


def test_record_route_rejects_invalid_id(completion_web):
    web, _ = completion_web

    response = web.get("/habits/not-an-id/record-completion")

    assert response.status_code == 404