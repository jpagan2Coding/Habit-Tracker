from datetime import datetime

import pytest

OWNER_EMAIL = "person@example.com"

UPDATED_FORM = {
    "name": "Evening Walk",
    "description": "Walk after dinner",
    "target": "2",
    "frequency": "weekly",
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


def test_edit_updates_existing_habit_without_duplicate(
    client, habits, owned_habit
):
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
    assert b"Evening Walk" in response.data
    assert b"Morning Walk" not in response.data


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
def test_invalid_edit_does_not_change_habit(
    client, habits, owned_habit, field, value
):
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


def test_leaving_edit_form_does_not_save(client, habits, owned_habit):
    original = habits.find_one({"_id": owned_habit})

    client.get(f"/habits/{owned_habit}/edit")
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert habits.find_one({"_id": owned_habit}) == original


def test_delete_removes_habit_and_shows_empty_state(
    client, habits, owned_habit
):
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
def test_mutation_requires_valid_csrf_token(
    client, habits, owned_habit, action, token
):
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
def test_other_user_cannot_access_or_modify_habit(
    client, habits, owned_habit, action, method
):
    token = form_token(client, owned_habit)
    original = habits.find_one({"_id": owned_habit})

    with client.session_transaction() as session:
        session["user_id"] = "another-user"
        session["user_email"] = "another@example.com"

    response = getattr(client, method)(
        f"/habits/{owned_habit}/{action}",
        **(
            {"data": {**UPDATED_FORM, "csrf_token": token}}
            if method == "post"
            else {}
        ),
    )

    assert response.status_code == 404
    assert habits.find_one({"_id": owned_habit}) == original


@pytest.mark.parametrize("action", ["edit", "delete"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_logged_out_user_is_redirected_to_login(
    client, habits, owned_habit, action, method
):
    original = habits.find_one({"_id": owned_habit})

    with client.session_transaction() as session:
        session.clear()

    response = getattr(client, method)(
        f"/habits/{owned_habit}/{action}"
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert habits.find_one({"_id": owned_habit}) == original


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_invalid_habit_id_returns_404(client, owned_habit, action):
    response = client.get(f"/habits/not-an-object-id/{action}")

    assert response.status_code == 404
