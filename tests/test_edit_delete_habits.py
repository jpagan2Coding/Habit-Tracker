from datetime import date, datetime
import pytest
from progress import target_on

OWNER_EMAIL = "person@example.com"

UPDATED_FORM = {
    "name": "Evening Walk",
    "description": "Walk after dinner",
    "target": "2",
    "frequency": "weekly",
    "reminder_enabled": "on",
    "reminder_time": "08:00",
    "reminder_timezone": "UTC",
}


@pytest.fixture
def owned_habit(client, habits):
    with client.session_transaction() as session:
        session["user_id"] = "test-owner"
        session["user_email"] = OWNER_EMAIL

    habit_id = habits.insert_one(
        {
            "name": "Morning Walk",
            "description": "Walk before breakfast",
            "target": 1,
            "frequency": "daily",
            "user_email": OWNER_EMAIL,
            "created_at": datetime(2026, 10, 1),
            "completed_dates": ["2026-10-01"],
        }
    ).inserted_id

    return habit_id


def form_token(client, habit_id):
    response = client.get(f"/habits/{habit_id}/edit")
    assert response.status_code == 200

    with client.session_transaction() as session:
        return session["habit_form_token"]


def test_edit_form_shows_current_values(client, owned_habit):
    response = client.get(f"/habits/{owned_habit}/edit")

    assert response.status_code == 200
    assert b"Morning Walk" in response.data
    assert b"Walk before breakfast" in response.data
    assert b"Save Changes" in response.data
    assert f'action="/habits/{owned_habit}/edit"'.encode() in response.data


def test_edit_updates_existing_habit_without_duplicate(client, habits, owned_habit):
    original = habits.find_one({"_id": owned_habit})
    token = form_token(client, owned_habit)

    response = client.post(
        f"/habits/{owned_habit}/edit",
        data={**UPDATED_FORM, "csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert habits.count_documents({}) == 1

    updated = habits.find_one({"_id": owned_habit})
    assert updated["name"] == "Evening Walk"
    assert updated["description"] == "Walk after dinner"
    assert updated["target"] == 2
    assert updated["frequency"] == "weekly"
    assert updated["user_email"] == original["user_email"]
    assert updated["created_at"] == original["created_at"]
    assert updated["completed_dates"] == original["completed_dates"]
    assert updated["reminder_enabled"] is True
    assert updated["reminder_time"] == "08:00"
    assert b"Evening Walk" in response.data
    assert b"Morning Walk" not in response.data


@pytest.mark.parametrize(
    ("frequency", "schedule"),
    [
        ("weekly", {"reminder_weekdays": ["friday"], "reminder_month_day": None}),
        ("monthly", {"reminder_weekdays": [], "reminder_month_day": 17}),
    ],
)
def test_edit_without_schedule_fields_preserves_existing_schedule(
    client, habits, owned_habit, frequency, schedule
):
    habits.update_one(
        {"_id": owned_habit},
        {
            "$set": {
                "frequency": frequency,
                "reminder_enabled": True,
                "reminder_time": "08:00",
                "reminder_timezone": "UTC",
                **schedule,
            }
        },
    )
    token = form_token(client, owned_habit)

    response = client.post(
        f"/habits/{owned_habit}/edit",
        data={**UPDATED_FORM, "frequency": frequency, "csrf_token": token},
    )

    assert response.status_code == 302
    updated = habits.find_one({"_id": owned_habit})
    assert updated["reminder_weekdays"] == schedule["reminder_weekdays"]
    assert updated["reminder_month_day"] == schedule["reminder_month_day"]


def test_editing_frequency_ignores_schedule_for_previous_frequency(
    client, habits, owned_habit
):
    habits.update_one(
        {"_id": owned_habit},
        {
            "$set": {
                "frequency": "weekly",
                "reminder_enabled": True,
                "reminder_time": "08:00",
                "reminder_timezone": "UTC",
                "reminder_weekdays": ["friday"],
            }
        },
    )
    token = form_token(client, owned_habit)

    response = client.post(
        f"/habits/{owned_habit}/edit",
        data={**UPDATED_FORM, "frequency": "daily", "csrf_token": token},
    )

    assert response.status_code == 302
    updated = habits.find_one({"_id": owned_habit})
    assert updated["reminder_weekdays"] == []
    assert updated["reminder_month_day"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("name", "x" * 101),
        ("target", ""),
        ("target", "0"),
        ("target", "1.5"),
        ("frequency", ""),
        ("frequency", "yearly"),
    ],
)
def test_invalid_edit_does_not_change_habit(client, habits, owned_habit, field, value):
    original = habits.find_one({"_id": owned_habit})
    token = form_token(client, owned_habit)

    response = client.post(
        f"/habits/{owned_habit}/edit",
        data={
            **UPDATED_FORM,
            field: value,
            "csrf_token": token,
        },
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": owned_habit}) == original
    assert habits.count_documents({}) == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target", "", "Target must be a whole number of 1 or more."),
        ("name", "x" * 101, "Habit name must be 100 characters or fewer."),
        ("reminder_time", "25:00", "Enter a valid reminder time."),
        ("reminder_timezone", "Invalid/Timezone", "Choose a valid time zone."),
    ],
)
def test_invalid_edit_form_preserves_validation_messages(
    client, owned_habit, field, value, message
):
    token = form_token(client, owned_habit)
    response = client.post(
        f"/habits/{owned_habit}/edit",
        data={**UPDATED_FORM, field: value, "csrf_token": token},
    )

    assert response.status_code == 400
    assert message.encode() in response.data


def test_leaving_edit_form_does_not_save(client, habits, owned_habit):
    original = habits.find_one({"_id": owned_habit})

    client.get(f"/habits/{owned_habit}/edit")
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert habits.find_one({"_id": owned_habit}) == original


def test_delete_removes_habit_and_shows_empty_state(client, habits, owned_habit):
    token = form_token(client, owned_habit)

    response = client.post(
        f"/habits/{owned_habit}/delete",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert habits.find_one({"_id": owned_habit}) is None
    assert b"No habits yet" in response.data


@pytest.mark.parametrize("action", ["edit", "delete"])
@pytest.mark.parametrize("token", [None, "incorrect-token"])
def test_mutation_requires_valid_csrf_token(client, habits, owned_habit, action, token):
    form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})
    data = dict(UPDATED_FORM)

    if token is not None:
        data["csrf_token"] = token

    response = client.post(
        f"/habits/{owned_habit}/{action}",
        data=data,
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": owned_habit}) == original


@pytest.mark.parametrize("action", ["edit", "delete"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_other_user_cannot_access_or_modify_habit(client, habits, owned_habit, action, method):
    token = form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    with client.session_transaction() as session:
        session["user_id"] = "another-user"
        session["user_email"] = "another@example.com"

    response = getattr(client, method)(
        f"/habits/{owned_habit}/{action}",
        **({"data": {**UPDATED_FORM, "csrf_token": token}} if method == "post" else {}),
    )

    assert response.status_code == 404
    assert habits.find_one({"_id": owned_habit}) == original


@pytest.mark.parametrize("action", ["edit", "delete"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_logged_out_user_is_redirected_to_login(client, habits, owned_habit, action, method):
    original = habits.find_one({"_id": owned_habit})

    with client.session_transaction() as session:
        session.clear()

    response = getattr(client, method)(f"/habits/{owned_habit}/{action}")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert habits.find_one({"_id": owned_habit}) == original


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_invalid_habit_id_returns_404(client, owned_habit, action):
    response = client.get(f"/habits/not-an-object-id/{action}")

    assert response.status_code == 404


def test_bulk_delete_removes_only_selected_owned_habits(client, habits, owned_habit):
    token = form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    second = dict(original)
    second.pop("_id")
    second["name"] = "Second Selected Habit"
    second_id = habits.insert_one(second).inserted_id

    unselected = dict(second)
    unselected.pop("_id", None)
    unselected["name"] = "Keep This Habit"
    unselected_id = habits.insert_one(unselected).inserted_id

    response = client.post(
        "/habits/delete-selected",
        data={
            "csrf_token": token,
            "habit_ids": [str(owned_habit), str(second_id)],
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert habits.find_one({"_id": owned_habit}) is None
    assert habits.find_one({"_id": second_id}) is None
    assert habits.find_one({"_id": unselected_id}) is not None

    with client.session_transaction() as session:
        assert (
            "success",
            "2 habits deleted successfully.",
        ) in session.get("_flashes", [])


def test_bulk_delete_preserves_other_users_habits(client, habits, owned_habit):
    token = form_token(client, owned_habit)

    other = dict(habits.find_one({"_id": owned_habit}))
    other.pop("_id")
    other["user_email"] = "another@example.com"
    other_id = habits.insert_one(other).inserted_id

    response = client.post(
        "/habits/delete-selected",
        data={
            "csrf_token": token,
            "habit_ids": [str(owned_habit), str(other_id)],
        },
    )

    assert response.status_code == 302
    assert habits.find_one({"_id": owned_habit}) is None
    assert habits.find_one({"_id": other_id}) is not None


@pytest.mark.parametrize("token", [None, "incorrect-token"])
def test_bulk_delete_requires_valid_csrf(client, habits, owned_habit, token):
    form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})
    data = {"habit_ids": [str(owned_habit)]}

    if token is not None:
        data["csrf_token"] = token

    response = client.post("/habits/delete-selected", data=data)

    assert response.status_code == 400
    assert habits.find_one({"_id": owned_habit}) == original


def test_bulk_delete_requires_login(client, habits, owned_habit):
    original = habits.find_one({"_id": owned_habit})

    with client.session_transaction() as session:
        session.clear()

    response = client.post(
        "/habits/delete-selected",
        data={"habit_ids": [str(owned_habit)]},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert habits.find_one({"_id": owned_habit}) == original


def test_bulk_delete_empty_selection_preserves_habits(client, habits, owned_habit):
    token = form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    response = client.post(
        "/habits/delete-selected",
        data={"csrf_token": token},
    )

    assert response.status_code == 302
    assert habits.find_one({"_id": owned_habit}) == original


def test_bulk_delete_invalid_id_prevents_all_deletion(client, habits, owned_habit):
    token = form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    response = client.post(
        "/habits/delete-selected",
        data={
            "csrf_token": token,
            "habit_ids": [str(owned_habit), "not-an-object-id"],
        },
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": owned_habit}) == original


def test_bulk_delete_duplicate_ids_count_once(client, habits, owned_habit):
    token = form_token(client, owned_habit)

    response = client.post(
        "/habits/delete-selected",
        data={
            "csrf_token": token,
            "habit_ids": [str(owned_habit), str(owned_habit)],
        },
    )

    assert response.status_code == 302
    assert habits.find_one({"_id": owned_habit}) is None

    with client.session_transaction() as session:
        assert (
            "success",
            "1 habit deleted successfully.",
        ) in session.get("_flashes", [])

    dashboard = client.get(response.headers["Location"]).data
    toast = dashboard.split(b'id="success-toast"', 1)[1].split(b"<main", 1)[0]
    message = b"1 habit deleted successfully."
    assert dashboard.count(message) == 1
    assert b'class="fixed ' in toast
    assert message in toast

@pytest.fixture
def daily_edit_day(monkeypatch):
    today = date(2026, 11, 22)
    monkeypatch.setattr("app.current_app_date", lambda: today)
    return today


def post_daily_target_edit(client, habit_id, target, **changes):
    token = form_token(client, habit_id)
    return client.post(
        f"/habits/{habit_id}/edit",
        data={
            "name": "Morning Walk",
            "description": "Walk before breakfast",
            "target": str(target),
            "frequency": "daily",
            "reminder_timezone": "UTC",
            "when_missed": "fail",
            "csrf_token": token,
            **changes,
        },
    )


def add_pending_daily_target(habits, habit_id):
    habits.update_one(
        {"_id": habit_id},
        {
            "$set": {
                "target": 2,
                "initial_target": 1,
                "target_history": [
                    {"effective_date": "2026-11-23", "target": 2}
                ],
                "goal_suggestion_last_decision": "2026-11-21",
            }
        },
    )


def test_daily_edit_form_explains_target_timing(
    client, habits, owned_habit, daily_edit_day
):
    add_pending_daily_target(habits, owned_habit)

    response = client.get(f"/habits/{owned_habit}/edit")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Today's target: 1." in html
    assert "Changes to this daily target start tomorrow." in html
    assert 'value="2"' in html


def test_daily_target_edit_persists_tomorrow_change(
    client, habits, owned_habit, daily_edit_day
):
    original = habits.find_one({"_id": owned_habit})

    response = post_daily_target_edit(client, owned_habit, 2)

    assert response.status_code == 302
    saved = habits.find_one({"_id": owned_habit})

    assert saved["target"] == 2
    assert saved["initial_target"] == 1
    assert saved["target_history"] == [
        {"effective_date": "2026-11-23", "target": 2}
    ]
    assert target_on(saved, date(2026, 11, 21)) == 1
    assert target_on(saved, daily_edit_day) == 1
    assert target_on(saved, date(2026, 11, 23)) == 2
    assert saved["completed_dates"] == original["completed_dates"]
    assert saved["created_at"] == original["created_at"]
    assert saved["user_email"] == original["user_email"]
    assert habits.count_documents({}) == 1


def test_description_edit_preserves_pending_daily_target(
    client, habits, owned_habit, daily_edit_day
):
    add_pending_daily_target(habits, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    response = post_daily_target_edit(
        client,
        owned_habit,
        2,
        description="Updated description",
    )

    assert response.status_code == 302
    saved = habits.find_one({"_id": owned_habit})

    assert saved["description"] == "Updated description"
    assert saved["initial_target"] == original["initial_target"]
    assert saved["target_history"] == original["target_history"]
    assert (
        saved["goal_suggestion_last_decision"]
        == original["goal_suggestion_last_decision"]
    )


def test_daily_target_edit_replaces_pending_change(
    client, habits, owned_habit, daily_edit_day
):
    add_pending_daily_target(habits, owned_habit)

    response = post_daily_target_edit(client, owned_habit, 3)

    assert response.status_code == 302
    saved = habits.find_one({"_id": owned_habit})

    assert saved["target"] == 3
    assert saved["target_history"] == [
        {"effective_date": "2026-11-23", "target": 3}
    ]
    assert target_on(saved, daily_edit_day) == 1
    assert target_on(saved, date(2026, 11, 23)) == 3


def test_daily_target_edit_can_cancel_pending_change(
    client, habits, owned_habit, daily_edit_day
):
    add_pending_daily_target(habits, owned_habit)

    response = post_daily_target_edit(client, owned_habit, 1)

    assert response.status_code == 302
    saved = habits.find_one({"_id": owned_habit})

    assert saved["target"] == 1
    assert saved["target_history"] == []
    assert target_on(saved, daily_edit_day) == 1
    assert target_on(saved, date(2026, 11, 23)) == 1


def test_dated_target_frequency_change_is_rejected_without_writing(
    client, habits, owned_habit, daily_edit_day
):
    add_pending_daily_target(habits, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    response = post_daily_target_edit(
        client,
        owned_habit,
        2,
        frequency="weekly",
    )

    assert response.status_code == 400
    assert b"Frequency cannot be changed" in response.data
    assert habits.find_one({"_id": owned_habit}) == original


def test_invalid_daily_edit_does_not_replace_pending_target(
    client, habits, owned_habit, daily_edit_day
):
    add_pending_daily_target(habits, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    response = post_daily_target_edit(
        client,
        owned_habit,
        3,
        name="",
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": owned_habit}) == original
