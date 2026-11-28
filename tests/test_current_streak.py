"""Tests for viewing one habit's current streak (Dashboard streak panel and habit history page)."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId

from progress import habit_streak, habit_summary

OWNER_EMAIL = "person@example.com"
CSRF = "test-csrf-token"

# a Wednesday (that week is Mon 11/2 - Sun 11/8)
TODAY = date(2026, 11, 4)


def make_habit(name="Drink water", frequency="daily", target=1, **fields):
    return {
        "name": name,
        "description": None,
        "target": target,
        "frequency": frequency,
        "user_email": OWNER_EMAIL,
        "created_at": datetime(2026, 10, 1),
        "completed_dates": [],
        **fields,
    }


def days_before(today, *offsets):
    return [(today - timedelta(days=offset)).isoformat() for offset in offsets]


# --- the streak calculation ----------------------------------------------------


def test_new_habit_starts_at_zero_and_invites_a_first_check_in():
    habit = make_habit(created_at=datetime(2026, 11, 4, 9))

    streak = habit_streak(habit, TODAY)

    assert streak["current_streak"] == 0
    assert streak["longest_streak"] == 0
    assert streak["unit"] == "day"
    assert streak["today_status"] == "in_progress"
    assert streak["starts_on"] is None


def test_habit_that_has_not_started_yet_reports_its_start_date():
    habit = make_habit(start_date="2026-11-10")

    streak = habit_streak(habit, TODAY)

    assert streak["current_streak"] == 0
    assert streak["starts_on"] == date(2026, 11, 10)
    assert streak["today_status"] is None
    assert streak["recent"] == []


def test_consecutive_completed_days_build_the_streak_and_today_does_not_break_it():
    habit = make_habit(completed_dates=days_before(TODAY, 1, 2, 3))

    streak = habit_streak(habit, TODAY)

    assert streak["current_streak"] == 3
    assert streak["today_status"] == "in_progress"


def test_completing_a_scheduled_day_increases_the_streak():
    habit = make_habit(completed_dates=days_before(TODAY, 1, 2, 3))
    before = habit_streak(habit, TODAY)["current_streak"]

    habit["completed_dates"].append(TODAY.isoformat())
    after = habit_streak(habit, TODAY)

    assert after["current_streak"] == before + 1
    assert after["today_status"] == "met"


def test_missed_date_resets_the_streak_but_keeps_the_longest():
    # done for 4 days, missed 11/1, then done 11/2 and 11/3
    habit = make_habit(completed_dates=days_before(TODAY, 1, 2, 4, 5, 6, 7))

    streak = habit_streak(habit, TODAY)

    assert streak["current_streak"] == 2
    assert streak["longest_streak"] == 4


def test_missing_yesterday_drops_the_streak_to_zero():
    habit = make_habit(completed_dates=days_before(TODAY, 2, 3, 4))

    assert habit_streak(habit, TODAY)["current_streak"] == 0


def test_skipped_day_neither_breaks_nor_extends_the_streak():
    habit = make_habit(
        completed_dates=days_before(TODAY, 1, 3),
        skipped_dates=days_before(TODAY, 2),
    )

    assert habit_streak(habit, TODAY)["current_streak"] == 2


def test_skipping_today_keeps_the_streak_safe():
    habit = make_habit(
        completed_dates=days_before(TODAY, 1, 2),
        skipped_dates=[TODAY.isoformat()],
    )

    streak = habit_streak(habit, TODAY)
    assert streak["current_streak"] == 2
    assert streak["today_status"] == "skipped"


def test_break_neither_breaks_nor_extends_the_streak():
    habit = make_habit(
        completed_dates=days_before(TODAY, 1, 5),
        breaks=[{"start": "2026-10-31", "end": "2026-11-02"}],
    )

    assert habit_streak(habit, TODAY)["current_streak"] == 2


def test_day_below_its_target_is_a_miss():
    habit = make_habit(
        target=2,
        completion_history=[
            {"date": "2026-11-02", "status": "completed", "count": 2},
            {"date": "2026-11-03", "status": "completed", "count": 1},
        ],
    )

    assert habit_streak(habit, TODAY)["current_streak"] == 0


def test_undoing_a_completion_recalculates_the_streak():
    habit = make_habit(
        completed_dates=days_before(TODAY, 0, 1, 2),
        completion_history=[{"date": TODAY.isoformat(), "status": "completed", "count": 1}],
    )
    assert habit_streak(habit, TODAY)["current_streak"] == 3

    habit["completion_history"] = [{"date": TODAY.isoformat(), "status": "undone", "count": 0}]
    habit["completed_dates"] = days_before(TODAY, 1, 2)

    assert habit_streak(habit, TODAY)["current_streak"] == 2


def test_backfilling_an_old_date_recalculates_the_streak():
    habit = make_habit(completed_dates=days_before(TODAY, 1, 3, 4))
    assert habit_streak(habit, TODAY)["current_streak"] == 1

    habit["completed_dates"].append(days_before(TODAY, 2)[0])

    assert habit_streak(habit, TODAY)["current_streak"] == 4


def test_weekly_streak_is_counted_in_weeks():
    habit = make_habit(
        frequency="weekly",
        target=2,
        created_at=datetime(2026, 10, 12),
        completed_dates=[
            "2026-10-13", "2026-10-15",  # week of 10/12
            "2026-10-20", "2026-10-22",  # week of 10/19
            "2026-10-27", "2026-10-29",  # week of 10/26
        ],
    )

    streak = habit_streak(habit, TODAY)

    assert streak["unit"] == "week"
    assert streak["current_streak"] == 3


def test_monthly_streak_is_counted_in_months():
    habit = make_habit(
        frequency="monthly",
        created_at=datetime(2026, 9, 1),
        completed_dates=["2026-09-05", "2026-10-05"],
    )

    streak = habit_streak(habit, TODAY)

    assert streak["unit"] == "month"
    assert streak["current_streak"] == 2


def test_recent_track_record_is_capped_and_oldest_first():
    habit = make_habit(completed_dates=days_before(TODAY, *range(1, 20)))

    recent = habit_streak(habit, TODAY)["recent"]

    assert len(recent) == 7
    assert recent[-1]["start"] == TODAY
    assert [period["start"] for period in recent] == sorted(period["start"] for period in recent)


def test_streak_matches_the_summary_page():
    habit = make_habit(
        completed_dates=days_before(TODAY, 1, 2, 4, 5, 6, 7, 9),
        skipped_dates=days_before(TODAY, 3),
    )

    streak = habit_streak(habit, TODAY)
    summary = habit_summary(habit, TODAY)

    assert streak["current_streak"] == summary["current_streak"]
    assert streak["longest_streak"] == summary["longest_streak"]


# --- Dashboard streak panel ----------------------------------------------------


@pytest.fixture
def real_today():
    return datetime.now(timezone.utc).date()


@pytest.fixture
def logged_in(client):
    with client.session_transaction() as session:
        session["user_id"] = str(ObjectId())
        session["user_email"] = OWNER_EMAIL
        session["username"] = "Test User"
        session["habit_form_token"] = CSRF
    return client


def add_habit(habits, name, completed_dates, created_at, **fields):
    habit = make_habit(name=name, completed_dates=completed_dates, created_at=created_at, **fields)
    return str(habits.insert_one(habit).inserted_id)


def streak_panel(response):
    html = response.get_data(as_text=True)
    return html.split('id="streak-panel"', 1)[1].split("</article>", 1)[0]


def slide_for(panel, habit_id):
    return panel.split(f'data-habit-id="{habit_id}"', 1)[1].split('class="streak-slide"', 1)[0]


def shown_count(panel, habit_id):
    return int(re.search(r"data-streak-count>(\d+)<", slide_for(panel, habit_id)).group(1))


def test_dashboard_replaces_the_placeholder_with_a_streak_panel(logged_in, habits, real_today):
    habit_id = add_habit(
        habits, "Read", days_before(real_today, 1, 2, 3), datetime(2026, 1, 1)
    )

    response = logged_in.get("/dashboard")
    panel = streak_panel(response)

    assert b"Coming in a future sprint" not in response.data
    assert "Current Streak" in panel
    assert "Read" in panel
    assert shown_count(panel, habit_id) == 3
    assert 'data-streak-unit>days<' in slide_for(panel, habit_id)
    assert "Consecutive days where you hit your target." in panel
    assert f'href="/habits/{habit_id}/history"' in panel


def test_dashboard_panel_has_one_slide_per_habit_in_dashboard_order(
    logged_in, habits, real_today
):
    older = add_habit(habits, "Stretch", [], datetime(2026, 1, 1))
    newer = add_habit(habits, "Journal", days_before(real_today, 1), datetime(2026, 2, 1))
    weekly = add_habit(habits, "Long run", [], datetime(2026, 3, 1), frequency="weekly")

    panel = streak_panel(logged_in.get("/dashboard"))

    assert panel.count('class="streak-slide"') == 3
    assert panel.index(weekly) < panel.index(newer) < panel.index(older)
    assert "1 / 3" in panel
    assert 'aria-label="Next habit"' in panel
    assert 'data-streak-unit>weeks<' in slide_for(panel, weekly)
    assert 'data-streak-unit>day<' in slide_for(panel, newer)


def test_single_habit_has_no_flip_buttons(logged_in, habits, real_today):
    add_habit(habits, "Read", [], datetime(2026, 1, 1))

    panel = streak_panel(logged_in.get("/dashboard"))

    assert 'aria-label="Next habit"' not in panel
    assert "Check in today to start a streak" in panel


def test_dashboard_panel_empty_state(logged_in):
    panel = streak_panel(logged_in.get("/dashboard"))

    assert "No habits yet · add one to start a streak" in panel


def test_completing_and_undoing_from_the_dashboard_recalculates_the_streak(
    logged_in, habits, real_today
):
    habit_id = add_habit(habits, "Read", days_before(real_today, 1, 2), datetime(2026, 1, 1))
    assert shown_count(streak_panel(logged_in.get("/dashboard")), habit_id) == 2

    assert logged_in.post(f"/habits/{habit_id}/complete", data={"csrf_token": CSRF}).status_code == 200
    panel = streak_panel(logged_in.get("/dashboard"))
    assert shown_count(panel, habit_id) == 3
    assert "In a row, including today" in slide_for(panel, habit_id)

    assert logged_in.post(f"/habits/{habit_id}/uncomplete", data={"csrf_token": CSRF}).status_code == 200
    assert shown_count(streak_panel(logged_in.get("/dashboard")), habit_id) == 2


def test_missed_overdue_habit_shows_zero_until_completed(logged_in, habits, real_today):
    habit_id = add_habit(
        habits,
        "Read",
        days_before(real_today, 2, 3),
        datetime(2026, 1, 1),
        when_missed="overdue",
    )
    assert shown_count(streak_panel(logged_in.get("/dashboard")), habit_id) == 0

    logged_in.post(f"/habits/{habit_id}/complete", data={"csrf_token": CSRF})

    assert shown_count(streak_panel(logged_in.get("/dashboard")), habit_id) == 1


def test_panel_only_shows_the_signed_in_users_habits(logged_in, habits, real_today):
    add_habit(habits, "Mine", [], datetime(2026, 1, 1))
    habits.insert_one({**make_habit(name="Someone else's"), "user_email": "other@example.com"})

    panel = streak_panel(logged_in.get("/dashboard"))

    assert "Mine" in panel
    assert "Someone else" not in panel


# --- habit details (history) page ------------------------------------------------


def test_history_page_shows_current_and_longest_streak(logged_in, habits, real_today):
    habit_id = add_habit(
        habits, "Read", days_before(real_today, 1, 2, 4, 5, 6, 7), datetime(2026, 1, 1)
    )

    html = logged_in.get(f"/habits/{habit_id}/history").get_data(as_text=True)
    section = html.split('id="habit-streak"', 1)[1].split("</section>", 1)[0]

    assert re.search(r"data-streak-count>2<", section)
    assert re.search(r"data-longest-streak>4<", section)
    assert "days in a row" in section
    assert "Consecutive days where you hit your target." in section
    assert "Missing a day resets the streak to 0." in section


def test_history_page_for_a_weekly_habit_uses_weeks(logged_in, habits):
    habit_id = add_habit(habits, "Long run", [], datetime(2026, 1, 1), frequency="weekly")

    html = logged_in.get(f"/habits/{habit_id}/history").get_data(as_text=True)

    assert "weeks in a row" in html
    assert "Consecutive weeks where you hit your target." in html
    assert "Check in this week to start a streak" in html
