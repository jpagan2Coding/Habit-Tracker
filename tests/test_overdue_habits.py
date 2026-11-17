from datetime import date, datetime, timedelta

import pytest

from app import is_overdue

OWNER_EMAIL = "person@example.com"

# a Wednesday: this week is Mon 11/16 - Sun 11/22, last week was Mon 11/9 - Sun 11/15
TODAY = date(2026, 11, 18)
YESTERDAY = "2026-11-17"

VALID_HABIT = {
    "name": "Evening walk",
    "description": "",
    "target": "1",
    "frequency": "daily",
}


def make_habit(when_missed="overdue", frequency="daily", target=1, **fields):
    habit = {
        "name": "Test habit",
        "frequency": frequency,
        "target": target,
        "created_at": datetime(2026, 10, 1),
        "completed_dates": [],
        "skipped_dates": [],
    }
    if when_missed:
        habit["when_missed"] = when_missed
    habit.update(fields)
    return habit


# which habits become overdue


def test_missed_daily_habit_set_to_overdue_is_overdue():
    assert is_overdue(make_habit(), TODAY)


def test_habit_set_to_fail_does_not_carry_over():
    assert not is_overdue(make_habit(when_missed="fail"), TODAY)


def test_habit_from_before_this_setting_does_not_carry_over():
    assert not is_overdue(make_habit(when_missed=None), TODAY)


def test_daily_habit_done_yesterday_is_not_overdue():
    assert not is_overdue(make_habit(completed_dates=[YESTERDAY]), TODAY)


def test_skipped_day_does_not_make_habit_overdue():
    assert not is_overdue(make_habit(skipped_dates=[YESTERDAY]), TODAY)


def test_weekly_habit_short_of_last_weeks_target_is_overdue():
    habit = make_habit(frequency="weekly", target=3, completed_dates=["2026-11-10", "2026-11-12"])

    assert is_overdue(habit, TODAY)


def test_weekly_habit_counts_skips_as_excused():
    habit = make_habit(
        frequency="weekly",
        target=3,
        completed_dates=["2026-11-10", "2026-11-12"],
        skipped_dates=["2026-11-14"],
    )

    assert not is_overdue(habit, TODAY)


def test_monthly_habit_missed_last_month_is_overdue():
    habit = make_habit(frequency="monthly", target=2, completed_dates=["2026-10-05"])

    assert is_overdue(habit, TODAY)


# clearing the overdue mark


def test_completing_overdue_habit_clears_it():
    assert not is_overdue(make_habit(completed_dates=["2026-11-18"]), TODAY)


def test_completing_weekly_habit_this_week_clears_it():
    habit = make_habit(frequency="weekly", target=3, completed_dates=["2026-11-16"])

    assert not is_overdue(habit, TODAY)


def test_skipping_overdue_habit_today_clears_it():
    assert not is_overdue(make_habit(skipped_dates=["2026-11-18"]), TODAY)


# habits that can't be overdue


@pytest.mark.parametrize(
    "fields",
    [
        {"start_date": "2026-11-18"},
        {"start_date": "2026-11-25"},
        {"created_at": datetime(2026, 11, 18, 9, 30)},
    ],
)
def test_habit_is_not_overdue_for_days_before_it_started(fields):
    assert not is_overdue(make_habit(**fields), TODAY)


def test_weekly_habit_started_partway_through_last_week_is_not_overdue():
    habit = make_habit(frequency="weekly", target=3, start_date="2026-11-12")

    assert not is_overdue(habit, TODAY)


def test_paused_habit_is_not_overdue():
    assert not is_overdue(make_habit(status="paused"), TODAY)


def test_day_on_a_break_does_not_make_habit_overdue():
    habit = make_habit(breaks=[{"start": "2026-11-12", "end": YESTERDAY}])

    assert not is_overdue(habit, TODAY)


def test_habit_on_a_break_today_is_not_overdue():
    habit = make_habit(breaks=[{"start": "2026-11-16", "end": "2026-11-20"}])

    assert not is_overdue(habit, TODAY)


def test_weekly_habit_on_a_break_most_of_last_week_is_not_overdue():
    habit = make_habit(
        frequency="weekly",
        target=3,
        completed_dates=["2026-11-09"],
        breaks=[{"start": "2026-11-10", "end": "2026-11-15"}],
    )

    assert not is_overdue(habit, TODAY)


# choosing the setting


def _form_token(client, habit_id):
    client.get(f"/habits/{habit_id}/edit")
    with client.session_transaction() as session:
        return session["habit_form_token"]


def test_create_habit_saves_overdue_choice(authenticated_client, habits):
    response = authenticated_client.post(
        "/habit-tracker", data={**VALID_HABIT, "when_missed": "overdue"}
    )

    assert response.status_code == 302
    assert habits.find_one({"name": "Evening walk"})["when_missed"] == "overdue"


def test_new_habit_counts_as_missed_by_default(authenticated_client, habits):
    authenticated_client.post("/habit-tracker", data=VALID_HABIT)

    assert habits.find_one({"name": "Evening walk"})["when_missed"] == "fail"


def test_invalid_choice_is_rejected(authenticated_client, habits):
    response = authenticated_client.post(
        "/habit-tracker", data={**VALID_HABIT, "when_missed": "sometimes"}
    )

    assert response.status_code == 400
    assert b"Choose what happens when this habit is missed." in response.data
    assert habits.count_documents({}) == 0


def test_edit_can_change_choice(authenticated_client, habits):
    habit_id = habits.insert_one(
        {**make_habit(when_missed="fail"), "user_email": OWNER_EMAIL}
    ).inserted_id
    token = _form_token(authenticated_client, habit_id)

    response = authenticated_client.post(
        f"/habits/{habit_id}/edit",
        data={**VALID_HABIT, "when_missed": "overdue", "csrf_token": token},
    )

    assert response.status_code == 302
    assert habits.find_one({"_id": habit_id})["when_missed"] == "overdue"


# dashboard


def _add_habit(habits, name, when_missed, days_old, completed_dates=None):
    created_at = datetime.utcnow() - timedelta(days=days_old)
    habits.insert_one(
        make_habit(
            when_missed=when_missed,
            name=name,
            user_email=OWNER_EMAIL,
            created_at=created_at,
            completed_dates=completed_dates or [],
        )
    )


def _todays_section(html):
    return html[html.index("Today's Habits") : html.index("All My Habits")]


def test_overdue_habit_is_marked_and_listed_first(authenticated_client, habits):
    _add_habit(habits, "Late habit", "overdue", days_old=10)
    _add_habit(habits, "Normal habit", "fail", days_old=5)

    todays = _todays_section(authenticated_client.get("/dashboard").get_data(as_text=True))

    assert "Overdue" in todays
    assert todays.index("Late habit") < todays.index("Normal habit")


def test_completed_overdue_habit_loses_its_mark(authenticated_client, habits):
    today = datetime.utcnow().date().isoformat()
    _add_habit(habits, "Late habit", "overdue", days_old=10, completed_dates=[today])

    todays = _todays_section(authenticated_client.get("/dashboard").get_data(as_text=True))

    assert "Late habit" in todays
    assert "Overdue" not in todays