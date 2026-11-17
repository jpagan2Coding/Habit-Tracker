"""Tests for pausing a habit: a temporary break that keeps streak data intact."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app import is_due_today
from models import PAUSE_DURATIONS
from progress import calculate_overall_progress, current_break
from reminders import reminder_due, send_due_reminders

OWNER_EMAIL = "person@example.com"

# a Wednesday
TODAY = date(2026, 11, 4)


def utc_today():
    return datetime.utcnow().date()


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


def break_from(start, end):
    return {"start": start.isoformat(), "end": end.isoformat()}


@pytest.fixture
def logged_in(client):
    with client.session_transaction() as session:
        session["user_id"] = "test-owner"
        session["user_email"] = OWNER_EMAIL


@pytest.fixture
def habit_id(habits, logged_in):
    return habits.insert_one(make_habit()).inserted_id


@pytest.fixture
def paused_habit_id(habits, logged_in):
    today = utc_today()
    habit = make_habit("Paused habit", breaks=[break_from(today, today + timedelta(days=6))])
    return habits.insert_one(habit).inserted_id


def form_token(client):
    response = client.get("/dashboard")
    assert response.status_code == 200

    with client.session_transaction() as session:
        return session["habit_form_token"]


def pause(client, habit_id, days, token):
    return client.post(
        f"/habits/{habit_id}/pause",
        data={"days": days, "csrf_token": token},
        follow_redirects=True,
    )


def todays_section(html):
    return html[html.index("Today's Habits") : html.index("All My Habits")]


def table_row(html, name):
    """The 'All My Habits' table row for one habit."""
    table = html[html.index("All My Habits") :]
    for row in table.split("<tr")[1:]:
        if name in row:
            return row
    raise AssertionError(f"{name} is not in the habits table")


# break dates


def test_break_covers_its_first_and_last_day_only():
    habit = make_habit(breaks=[break_from(date(2026, 11, 4), date(2026, 11, 6))])

    assert current_break(habit, date(2026, 11, 3)) is None
    assert current_break(habit, date(2026, 11, 4)) == {"start": "2026-11-04", "end": "2026-11-06"}
    assert current_break(habit, date(2026, 11, 6)) is not None
    assert current_break(habit, date(2026, 11, 7)) is None


def test_habit_without_breaks_is_not_on_break():
    assert current_break(make_habit(), TODAY) is None


# pausing with a chosen timeframe


def test_active_habit_has_pause_action_on_dashboard(client, habit_id):
    html = client.get("/dashboard").get_data(as_text=True)

    assert f'href="/habits/{habit_id}/pause"' in todays_section(html)
    assert f'href="/habits/{habit_id}/pause"' in table_row(html, "Drink water")


def test_pause_page_offers_each_timeframe(client, habit_id):
    response = client.get(f"/habits/{habit_id}/pause")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Start break" in html
    for days, label in PAUSE_DURATIONS.items():
        assert f'value="{days}"' in html
        assert label in html


@pytest.mark.parametrize("days", list(PAUSE_DURATIONS))
def test_pausing_starts_a_break_for_the_chosen_timeframe(client, habits, habit_id, days):
    today = utc_today()
    token = form_token(client)

    response = pause(client, habit_id, days, token)

    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert "is on a break" in response.get_data(as_text=True)
    assert habits.find_one({"_id": habit_id})["breaks"] == [
        break_from(today, today + timedelta(days=days - 1))
    ]


@pytest.mark.parametrize("days", ["", "0", "2", "-1", "week"])
def test_pause_requires_a_listed_timeframe(client, habits, habit_id, days):
    token = form_token(client)

    response = pause(client, habit_id, days, token)

    assert response.status_code == 400
    assert b"Choose how long the break should last." in response.data
    assert "breaks" not in habits.find_one({"_id": habit_id})


def test_habit_on_break_cannot_be_paused_again(client, habits, paused_habit_id):
    token = form_token(client)
    original = habits.find_one({"_id": paused_habit_id})["breaks"]

    response = pause(client, paused_habit_id, 30, token)

    assert b"already on a break" in response.data
    assert habits.find_one({"_id": paused_habit_id})["breaks"] == original


# during the break


def test_habit_on_break_is_not_due():
    habit = make_habit(breaks=[break_from(TODAY, TODAY + timedelta(days=2))])

    assert not is_due_today(habit, TODAY)


def test_habit_completed_before_pausing_stays_on_todays_list():
    habit = make_habit(
        completed_dates=[TODAY.isoformat()],
        breaks=[break_from(TODAY, TODAY + timedelta(days=2))],
    )

    assert is_due_today(habit, TODAY)


def test_habit_on_break_is_greyed_out_on_dashboard(client, habits, paused_habit_id):
    habits.insert_one(make_habit("Active habit"))
    resume_date = utc_today() + timedelta(days=7)

    html = client.get("/dashboard").get_data(as_text=True)
    paused_row = table_row(html, "Paused habit")
    active_row = table_row(html, "Active habit")

    assert "Paused habit" not in todays_section(html)
    assert "Active habit" in todays_section(html)
    assert paused_row.startswith(' class="habit-paused"')
    assert "habit-paused" not in active_row
    assert "On break" in paused_row
    assert f"Back on {resume_date:%B %d, %Y}" in paused_row
    assert f'action="/habits/{paused_habit_id}/resume"' in paused_row
    assert f'href="/habits/{paused_habit_id}/pause"' not in html
    assert "1 active · 1 on a break" in html


def test_dashboard_explains_when_every_habit_is_on_a_break(client, paused_habit_id):
    html = client.get("/dashboard").get_data(as_text=True)

    assert "Nothing scheduled for today" in html
    assert "All of your habits are on a break." in html


def test_habit_on_break_cannot_be_completed(client, habits, paused_habit_id):
    token = form_token(client)

    response = client.post(f"/habits/{paused_habit_id}/complete", data={"csrf_token": token})

    assert response.status_code == 409
    assert response.json["error"] == "Habit is on a break"
    assert habits.find_one({"_id": paused_habit_id})["completed_dates"] == []


def test_reminder_is_not_due_during_break_and_returns_after():
    habit = make_habit(
        reminder_time="09:30",
        reminder_timezone="UTC",
        breaks=[break_from(date(2026, 11, 4), date(2026, 11, 6))],
    )

    assert reminder_due(habit, datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)) is None
    assert reminder_due(habit, datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)) is None
    assert (
        reminder_due(habit, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc)) == "2026-11-07:09:30"
    )


@pytest.mark.parametrize(
    "break_day",
    [
        date(2026, 11, 4),  # the user's local day is on the break
        date(2026, 11, 5),  # the app's UTC day is on the break
    ],
)
def test_reminder_stays_quiet_when_local_or_utc_day_is_on_break(break_day):
    habit = make_habit(
        reminder_time="21:00",
        reminder_timezone="America/New_York",
        breaks=[break_from(break_day, break_day)],
    )
    # 21:00 in New York on Nov 4 is 02:00 UTC on Nov 5
    now = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)

    assert reminder_due(habit, now) is None


def test_break_creates_no_reminder_notifications(mongo_client):
    database = mongo_client["Habit-Tracker_test"]
    database["habits"].insert_one(
        make_habit(
            "Read",
            reminder_enabled=True,
            reminder_time="09:30",
            reminder_timezone="UTC",
            breaks=[break_from(date(2026, 11, 1), date(2026, 11, 7))],
        )
    )

    now = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)

    assert send_due_reminders(database, now) == 0
    assert database["notifications"].count_documents({}) == 0


def test_pausing_leaves_streak_data_untouched(client, habits, logged_in):
    yesterday = (utc_today() - timedelta(days=1)).isoformat()
    history = {
        "completed_dates": ["2026-10-01", "2026-10-02", yesterday],
        "skipped_dates": ["2026-10-03"],
    }
    habit_id = habits.insert_one(make_habit(**history)).inserted_id
    token = form_token(client)

    pause(client, habit_id, 7, token)

    habit = habits.find_one({"_id": habit_id})
    assert habit["completed_dates"] == history["completed_dates"]
    assert habit["skipped_dates"] == history["skipped_dates"]


def test_break_days_do_not_count_against_progress():
    habit = make_habit(
        completed_dates=["2026-10-01", "2026-10-02"],
        breaks=[break_from(date(2026, 10, 3), date(2026, 10, 7))],
    )

    result = calculate_overall_progress([habit], end_date=date(2026, 10, 7), period_days=7)

    assert result["scheduled"] == 2
    assert result["completed"] == 2
    assert result["percentage"] == 100


def test_completion_logged_before_a_break_still_counts_toward_progress():
    habit = make_habit(
        completed_dates=["2026-10-07"],
        breaks=[break_from(date(2026, 10, 7), date(2026, 10, 9))],
    )

    result = calculate_overall_progress([habit], end_date=date(2026, 10, 7), period_days=7)

    assert result["scheduled"] == 7
    assert result["completed"] == 1


# resuming


def test_habit_is_due_again_once_break_elapses():
    habit = make_habit(breaks=[break_from(TODAY - timedelta(days=3), TODAY - timedelta(days=1))])

    assert is_due_today(habit, TODAY)


def test_habit_returns_to_dashboard_once_break_elapses(client, habits, logged_in):
    today = utc_today()
    habit_id = habits.insert_one(
        make_habit(
            "Back from break",
            breaks=[break_from(today - timedelta(days=3), today - timedelta(days=1))],
        )
    ).inserted_id

    html = client.get("/dashboard").get_data(as_text=True)
    row = table_row(html, "Back from break")

    assert f'action="/habits/{habit_id}/complete"' in todays_section(html)
    assert "habit-paused" not in row
    assert "On break" not in row
    assert f'href="/habits/{habit_id}/pause"' in row


def test_ending_break_early_returns_habit_and_keeps_history(client, habits, logged_in):
    today = utc_today()
    history = {
        "completed_dates": ["2026-10-01", (today - timedelta(days=3)).isoformat()],
        "skipped_dates": ["2026-10-02"],
    }
    earlier_break = break_from(date(2026, 10, 10), date(2026, 10, 12))
    habit_id = habits.insert_one(
        make_habit(
            breaks=[
                earlier_break,
                break_from(today - timedelta(days=2), today + timedelta(days=4)),
            ],
            **history,
        )
    ).inserted_id
    token = form_token(client)

    response = client.post(
        f"/habits/{habit_id}/resume",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    habit = habits.find_one({"_id": habit_id})
    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert "is active again" in response.get_data(as_text=True)
    assert f'action="/habits/{habit_id}/complete"' in todays_section(
        response.get_data(as_text=True)
    )
    assert habit["breaks"] == [
        earlier_break,
        break_from(today - timedelta(days=2), today - timedelta(days=1)),
    ]
    assert habit["completed_dates"] == history["completed_dates"]
    assert habit["skipped_dates"] == history["skipped_dates"]


def test_ending_a_break_that_started_today_removes_it(client, habits, habit_id):
    token = form_token(client)

    pause(client, habit_id, 7, token)
    client.post(f"/habits/{habit_id}/resume", data={"csrf_token": token})

    assert habits.find_one({"_id": habit_id})["breaks"] == []
    assert is_due_today(habits.find_one({"_id": habit_id}), utc_today())


def test_ending_break_on_active_habit_changes_nothing(client, habits, habit_id):
    token = form_token(client)

    response = client.post(
        f"/habits/{habit_id}/resume",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert b"isn&#39;t on a break" in response.data
    assert "breaks" not in habits.find_one({"_id": habit_id})


# access control


@pytest.mark.parametrize("action", ["pause", "resume"])
@pytest.mark.parametrize("token", [None, "incorrect-token"])
def test_requires_valid_csrf_token(client, habits, logged_in, action, token):
    today = utc_today()
    breaks = [] if action == "pause" else [break_from(today, today)]
    habit_id = habits.insert_one(make_habit(breaks=breaks)).inserted_id
    form_token(client)
    original = habits.find_one({"_id": habit_id})
    data = {"days": 7} if token is None else {"days": 7, "csrf_token": token}

    response = client.post(f"/habits/{habit_id}/{action}", data=data)

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_other_user_cannot_change_habit(client, habits, logged_in, action):
    today = utc_today()
    breaks = [] if action == "pause" else [break_from(today, today)]
    habit_id = habits.insert_one(make_habit(breaks=breaks)).inserted_id
    token = form_token(client)
    original = habits.find_one({"_id": habit_id})

    with client.session_transaction() as session:
        session["user_id"] = "another-user"
        session["user_email"] = "another@example.com"

    response = client.post(f"/habits/{habit_id}/{action}", data={"days": 7, "csrf_token": token})

    assert response.status_code == 404
    assert habits.find_one({"_id": habit_id}) == original


def test_other_user_cannot_open_pause_page(client, habits, logged_in):
    habit_id = habits.insert_one(make_habit(user_email="another@example.com")).inserted_id

    assert client.get(f"/habits/{habit_id}/pause").status_code == 404


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_logged_out_user_is_redirected_to_login(client, habits, action):
    habit_id = habits.insert_one(make_habit()).inserted_id

    response = client.post(f"/habits/{habit_id}/{action}", data={"days": 7})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert "breaks" not in habits.find_one({"_id": habit_id})
