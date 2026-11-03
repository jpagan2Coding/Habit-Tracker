from datetime import date, datetime

import pytest

from app import is_due_today

# a Wednesday (that week is Mon 11/2 - Sun 11/8)
TODAY = date(2026, 11, 4)


def make_habit(frequency="daily", target=1, completed_dates=None, status=None):
    habit = {
        "name": "Test habit",
        "frequency": frequency,
        "target": target,
        "completed_dates": completed_dates or [],
    }
    if status:
        habit["status"] = status
    return habit


# scheduling rules

def test_daily_habit_is_due_today():
    assert is_due_today(make_habit("daily"), TODAY)


def test_completed_habit_stays_on_todays_list():
    habit = make_habit("daily", completed_dates=["2026-11-04"])
    assert is_due_today(habit, TODAY)


def test_weekly_habit_due_until_target_met_this_week():
    habit = make_habit("weekly", target=3, completed_dates=["2026-11-02", "2026-11-03"])
    assert is_due_today(habit, TODAY)


def test_weekly_habit_not_due_after_target_met_this_week():
    habit = make_habit("weekly", target=2, completed_dates=["2026-11-02", "2026-11-03"])
    assert not is_due_today(habit, TODAY)


def test_last_weeks_completions_do_not_count():
    habit = make_habit("weekly", target=1, completed_dates=["2026-11-01"])
    assert is_due_today(habit, TODAY)


def test_monthly_habit_not_due_after_target_met_this_month():
    habit = make_habit("monthly", target=1, completed_dates=["2026-11-01"])
    assert not is_due_today(habit, TODAY)


def test_last_months_completions_do_not_count():
    habit = make_habit("monthly", target=1, completed_dates=["2026-10-31"])
    assert is_due_today(habit, TODAY)


@pytest.mark.parametrize("status", ["paused", "archived", "expired"])
def test_inactive_habits_are_not_due(status):
    assert not is_due_today(make_habit("daily", status=status), TODAY)


# dashboard

def _add_habit(habits, name, **fields):
    habits.insert_one(
        {
            "name": name,
            "description": None,
            "target": 1,
            "frequency": "daily",
            "user_email": "person@example.com",
            "created_at": datetime.utcnow(),
            "completed_dates": [],
            **fields,
        }
    )


def _todays_section(html):
    # just the "Today's Habits" part of the page
    return html[html.index("Today's Habits"):html.index("All My Habits")]


def test_dashboard_lists_only_habits_due_today(authenticated_client, habits):
    today = datetime.utcnow().date().isoformat()
    _add_habit(habits, "Drink water")
    _add_habit(habits, "Paused habit", status="paused")
    _add_habit(habits, "Done for the month", frequency="monthly", completed_dates=[today])

    response = authenticated_client.get("/dashboard")
    todays = _todays_section(response.get_data(as_text=True))

    assert "Drink water" in todays
    assert "Done for the month" in todays
    assert "Paused habit" not in todays


def test_completed_habit_looks_different(authenticated_client, habits):
    today = datetime.utcnow().date().isoformat()
    _add_habit(habits, "Finished habit", completed_dates=[today])

    response = authenticated_client.get("/dashboard")
    todays = _todays_section(response.get_data(as_text=True))

    assert "line-through" in todays
    assert "Completed" in todays


def test_message_when_nothing_scheduled_today(authenticated_client, habits):
    _add_habit(habits, "Paused habit", status="paused")

    response = authenticated_client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert "Nothing scheduled for today" in html
    assert "No habits yet" not in html


def test_new_user_still_sees_no_habits_yet(authenticated_client):
    response = authenticated_client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert "No habits yet" in html
    assert "Nothing scheduled for today" not in html
