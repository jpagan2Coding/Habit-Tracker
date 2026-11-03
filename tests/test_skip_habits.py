from datetime import date, datetime

import pytest

from app import is_due_today

OWNER_EMAIL = "person@example.com"

# a Wednesday (that week is Mon 11/2 - Sun 11/8)
TODAY = date(2026, 11, 4)


def today_iso():
    return datetime.utcnow().date().isoformat()


def make_habit(name="Drink water", **fields):
    return {
        "name": name,
        "description": None,
        "target": 1,
        "frequency": "daily",
        "user_email": OWNER_EMAIL,
        "created_at": datetime(2026, 10, 1),
        "completed_dates": [],
        **fields,
    }


@pytest.fixture
def logged_in(client):
    with client.session_transaction() as session:
        session["user_id"] = "test-owner"
        session["user_email"] = OWNER_EMAIL


@pytest.fixture
def habit_id(habits, logged_in):
    return habits.insert_one(make_habit()).inserted_id


def form_token(client):
    response = client.get("/dashboard")
    assert response.status_code == 200

    with client.session_transaction() as session:
        return session["habit_form_token"]


def todays_section(response):
    html = response.get_data(as_text=True)
    return html[html.index("Today's Habits") : html.index("All My Habits")]


# scheduling rules


def test_skipped_habit_stays_on_todays_list():
    habit = make_habit(skipped_dates=["2026-11-04"])
    assert is_due_today(habit, TODAY)


def test_skipped_days_do_not_count_toward_target():
    habit = make_habit(frequency="weekly", target=1, skipped_dates=["2026-11-02"])
    assert is_due_today(habit, TODAY)


@pytest.mark.parametrize("status", ["paused", "archived", "expired"])
def test_inactive_habit_with_skip_is_not_due(status):
    habit = make_habit(status=status, skipped_dates=["2026-11-04"])
    assert not is_due_today(habit, TODAY)


# skip action


def test_scheduled_habit_has_skip_action(client, habit_id):
    todays = todays_section(client.get("/dashboard"))

    assert f'action="/habits/{habit_id}/skip"' in todays
    assert "Undo skip" not in todays


def test_completed_habit_has_no_skip_action(client, habits, logged_in):
    habit_id = habits.insert_one(make_habit(completed_dates=[today_iso()])).inserted_id

    todays = todays_section(client.get("/dashboard"))

    assert f'action="/habits/{habit_id}/skip"' not in todays


def test_skip_saves_todays_date_on_that_habit_only(client, habits, habit_id):
    other_id = habits.insert_one(make_habit("Read")).inserted_id
    token = form_token(client)

    response = client.post(
        f"/habits/{habit_id}/skip",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert habits.find_one({"_id": habit_id})["skipped_dates"] == [today_iso()]
    assert habits.find_one({"_id": habit_id})["completed_dates"] == []
    assert "skipped_dates" not in habits.find_one({"_id": other_id})


def test_skipping_twice_saves_the_date_once(client, habits, habit_id):
    token = form_token(client)

    client.post(f"/habits/{habit_id}/skip", data={"csrf_token": token})
    client.post(f"/habits/{habit_id}/skip", data={"csrf_token": token})

    assert habits.find_one({"_id": habit_id})["skipped_dates"] == [today_iso()]


def test_skipped_status_differs_from_completed_and_not_done(client, habits, logged_in):
    habits.insert_one(make_habit("Skipped habit", skipped_dates=[today_iso()]))

    todays = todays_section(client.get("/dashboard"))

    assert "Skipped today" in todays
    assert "Undo skip" in todays
    assert "line-through" not in todays
    assert "Completed" not in todays


def test_completed_habit_cannot_be_skipped(client, habits, logged_in):
    habit_id = habits.insert_one(make_habit(completed_dates=[today_iso()])).inserted_id
    token = form_token(client)

    response = client.post(
        f"/habits/{habit_id}/skip",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert b"already completed today" in response.data
    assert "skipped_dates" not in habits.find_one({"_id": habit_id})


def test_habit_not_scheduled_today_cannot_be_skipped(client, habits, logged_in):
    habit_id = habits.insert_one(make_habit(status="paused")).inserted_id
    token = form_token(client)

    response = client.post(
        f"/habits/{habit_id}/skip",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert b"isn&#39;t scheduled for today" in response.data
    assert "skipped_dates" not in habits.find_one({"_id": habit_id})


# progress rule: skipped habits are neutral


def test_skipped_habit_drops_out_of_todays_progress(client, habits, logged_in):
    habits.insert_one(make_habit("Done", completed_dates=[today_iso()]))
    habits.insert_one(make_habit("Skipped", skipped_dates=[today_iso()]))

    html = client.get("/dashboard").get_data(as_text=True)

    assert "Out of 1 · 100%" in html
    assert "1 skipped" in html


def test_all_habits_skipped_shows_zero_progress(client, habits, logged_in):
    habits.insert_one(make_habit("Skipped", skipped_dates=[today_iso()]))

    html = client.get("/dashboard").get_data(as_text=True)

    assert "Out of 0 · 0%" in html


# undo skip


def test_undo_skip_returns_habit_to_incomplete(client, habits, logged_in):
    habit_id = habits.insert_one(make_habit(skipped_dates=["2026-10-01", today_iso()])).inserted_id
    token = form_token(client)

    response = client.post(
        f"/habits/{habit_id}/unskip",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert habits.find_one({"_id": habit_id})["skipped_dates"] == ["2026-10-01"]
    todays = todays_section(response)
    assert f'action="/habits/{habit_id}/skip"' in todays
    assert "Skipped today" not in todays


# access control


@pytest.mark.parametrize("action", ["skip", "unskip"])
@pytest.mark.parametrize("token", [None, "incorrect-token"])
def test_requires_valid_csrf_token(client, habits, logged_in, action, token):
    habit_id = habits.insert_one(make_habit(skipped_dates=[today_iso()])).inserted_id
    form_token(client)
    original = habits.find_one({"_id": habit_id})
    data = {} if token is None else {"csrf_token": token}

    response = client.post(f"/habits/{habit_id}/{action}", data=data)

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("action", ["skip", "unskip"])
def test_other_user_cannot_change_habit(client, habits, logged_in, action):
    habit_id = habits.insert_one(make_habit(skipped_dates=[today_iso()])).inserted_id
    token = form_token(client)
    original = habits.find_one({"_id": habit_id})

    with client.session_transaction() as session:
        session["user_id"] = "another-user"
        session["user_email"] = "another@example.com"

    response = client.post(f"/habits/{habit_id}/{action}", data={"csrf_token": token})

    assert response.status_code == 404
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("action", ["skip", "unskip"])
def test_logged_out_user_is_redirected_to_login(client, habits, action):
    habit_id = habits.insert_one(make_habit()).inserted_id

    response = client.post(f"/habits/{habit_id}/{action}")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert "skipped_dates" not in habits.find_one({"_id": habit_id})
