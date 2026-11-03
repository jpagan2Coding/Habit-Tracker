from datetime import datetime

import pytest


OWNER_EMAIL = "person@example.com"
OTHER_EMAIL = "another@example.com"


def make_habit(name, user_email):
    return {
        "name": name,
        "description": None,
        "target": 1,
        "frequency": "daily",
        "user_email": user_email,
        "created_at": datetime(2026, 10, 1),
        "completed_dates": ["2026-10-01", "2026-10-02"],
    }


@pytest.fixture
def logged_in(client):
    with client.session_transaction() as session:
        session["user_id"] = "test-owner"
        session["user_email"] = OWNER_EMAIL


@pytest.fixture
def seeded_habits(habits):
    habits.insert_many(
        [
            make_habit("Morning Walk", OWNER_EMAIL),
            make_habit("Read", OWNER_EMAIL),
            make_habit("Someone Else's Habit", OTHER_EMAIL),
        ]
    )


def form_token(client):
    response = client.get("/account/delete-data")
    assert response.status_code == 200

    with client.session_transaction() as session:
        return session["habit_form_token"]


def test_confirmation_page_shows_habit_count(client, logged_in, seeded_habits):
    response = client.get("/account/delete-data")

    assert response.status_code == 200
    assert b"Delete All My Data?" in response.data
    assert b"all 2 of your habits" in response.data


def test_delete_wipes_habits_and_history_but_keeps_account(
    client, mongo_client, habits, logged_in, seeded_habits
):
    users = mongo_client["Habit-Tracker_test"]["users"]
    token = form_token(client)

    response = client.post(
        "/account/delete-data",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert b"All of your habit data has been deleted." in response.data
    assert b"No habits yet" in response.data
    assert habits.count_documents({"user_email": OWNER_EMAIL}) == 0
    assert users.find_one({"email": OWNER_EMAIL}) is not None

    with client.session_transaction() as session:
        assert session["user_email"] == OWNER_EMAIL


def test_delete_leaves_other_users_data(client, habits, logged_in, seeded_habits):
    token = form_token(client)

    client.post("/account/delete-data", data={"csrf_token": token})

    remaining = list(habits.find())
    assert [habit["user_email"] for habit in remaining] == [OTHER_EMAIL]
    assert remaining[0]["completed_dates"] == ["2026-10-01", "2026-10-02"]


@pytest.mark.parametrize("token", [None, "incorrect-token"])
def test_delete_requires_valid_csrf_token(
    client, habits, logged_in, seeded_habits, token
):
    form_token(client)
    data = {} if token is None else {"csrf_token": token}

    response = client.post("/account/delete-data", data=data)

    assert response.status_code == 400
    assert habits.count_documents({"user_email": OWNER_EMAIL}) == 2


@pytest.mark.parametrize("method", ["get", "post"])
def test_logged_out_user_is_redirected_to_login(
    client, habits, seeded_habits, method
):
    response = getattr(client, method)("/account/delete-data")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert habits.count_documents({}) == 3
