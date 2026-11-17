"""Tests for the per-habit Summary tab: streaks, longest streak, and completion rate."""

import re
from datetime import date, datetime, timedelta

import pytest

from progress import habit_periods, habit_summary

OWNER_EMAIL = "person@example.com"

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


def days(*pairs):
    """ISO dates for (month, day) pairs in 2026."""
    return [date(2026, month, day).isoformat() for month, day in pairs]


def day_range(first, last):
    return [
        (first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)
    ]


def summary_for(habit, period_days=7, today=TODAY):
    return habit_summary(habit, today, period_days)


# streaks: daily habits


def test_consecutive_days_make_a_streak_and_today_in_progress_does_not_break_it():
    summary = summary_for(make_habit(completed_dates=days((11, 1), (11, 2), (11, 3))))

    assert summary["current_streak"] == 3
    assert summary["longest_streak"] == 3
    assert summary["unit"] == "day"


def test_completing_today_extends_the_streak():
    summary = summary_for(make_habit(completed_dates=days((11, 1), (11, 2), (11, 3), (11, 4))))

    assert summary["current_streak"] == 4


def test_missed_day_resets_current_streak_but_longest_is_kept():
    completed = day_range(date(2026, 10, 20), date(2026, 10, 25)) + days((11, 2), (11, 3))

    summary = summary_for(make_habit(completed_dates=completed))

    assert summary["current_streak"] == 2
    assert summary["longest_streak"] == 6


def test_missing_yesterday_ends_the_current_streak():
    summary = summary_for(make_habit(completed_dates=days((11, 1), (11, 2))))

    assert summary["current_streak"] == 0
    assert summary["longest_streak"] == 2


def test_skipped_day_neither_breaks_nor_extends_a_streak():
    habit = make_habit(completed_dates=days((11, 1), (11, 3)), skipped_dates=days((11, 2)))

    summary = summary_for(habit)

    assert summary["current_streak"] == 2


# streaks: weekly and monthly habits


def test_weekly_streak_counts_weeks_that_hit_the_target():
    habit = make_habit(
        frequency="weekly",
        target=2,
        # weeks of Oct 19 and Oct 26 hit 2; this week has 1 so far
        completed_dates=days((10, 20), (10, 22), (10, 27), (10, 29), (11, 3)),
    )

    summary = summary_for(habit)

    assert summary["unit"] == "week"
    assert summary["current_streak"] == 2
    assert summary["longest_streak"] == 2


def test_weeks_start_on_monday_like_todays_list():
    # Sunday Nov 1 closes the week of Oct 26; Monday Nov 2 opens a new one
    habit = make_habit(frequency="weekly", completed_dates=days((11, 1), (11, 2)))

    assert summary_for(habit)["current_streak"] == 2


def test_week_short_of_its_target_breaks_the_streak():
    habit = make_habit(
        frequency="weekly",
        target=2,
        completed_dates=days((10, 20), (10, 22), (10, 27)),
    )

    assert summary_for(habit)["current_streak"] == 0


def test_skip_lowers_that_periods_target():
    habit = make_habit(
        frequency="weekly",
        target=2,
        completed_dates=days((10, 27)),
        skipped_dates=days((10, 28)),
    )

    assert summary_for(habit)["current_streak"] == 1


def test_monthly_streak_and_year_boundary():
    habit = make_habit(
        frequency="monthly",
        created_at=datetime(2025, 12, 10),
        completed_dates=["2025-12-12", "2026-01-05"],
    )

    periods = habit_periods(habit, date(2026, 2, 15))
    summary = summary_for(habit, today=date(2026, 2, 15))

    assert [period["start"] for period in periods] == [
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert periods[0]["end"] == date(2025, 12, 31)
    assert summary["unit"] == "month"
    assert summary["current_streak"] == 2


# completion rate


def test_completion_rate_covers_selected_window_and_skips_unfinished_today():
    habit = make_habit(completed_dates=days((10, 29), (10, 30), (10, 31), (11, 2)))

    summary = summary_for(habit, period_days=7)

    # Oct 29 - Nov 3 are finished; today (Nov 4) is still in progress
    assert (summary["completed"], summary["expected"], summary["percentage"]) == (4, 6, 67)


def test_finished_today_counts_toward_completion_rate():
    habit = make_habit(completed_dates=days((10, 29), (10, 30), (10, 31), (11, 2), (11, 4)))

    summary = summary_for(habit, period_days=7)

    assert (summary["completed"], summary["expected"], summary["percentage"]) == (5, 7, 71)


@pytest.mark.parametrize(("period_days", "expected"), [(7, 6), (30, 29), (90, 34)])
def test_completion_rate_follows_selected_period(period_days, expected):
    # created Oct 1, so the 90-day window only reaches back to then
    summary = summary_for(make_habit(), period_days=period_days)

    assert summary["expected"] == expected
    assert summary["percentage"] == 0


def test_skipped_days_count_as_incomplete_in_the_completion_rate():
    habit = make_habit(completed_dates=days((11, 2)), skipped_dates=days((11, 3)))

    summary = summary_for(habit, period_days=7)

    assert (summary["completed"], summary["expected"]) == (1, 6)


def test_days_before_the_habit_started_are_not_counted():
    habit = make_habit(created_at=datetime(2026, 11, 2), completed_dates=days((11, 2)))

    summary = summary_for(habit, period_days=30)

    assert (summary["completed"], summary["expected"], summary["percentage"]) == (1, 2, 50)


def test_invalid_period_is_rejected():
    with pytest.raises(ValueError):
        summary_for(make_habit(), period_days=14)


# start dates


def test_habit_with_a_future_start_date_has_no_history_yet():
    habit = make_habit(start_date="2026-11-10")

    summary = summary_for(habit)

    assert not summary["has_history"]
    assert summary["starts_on"] == date(2026, 11, 10)
    assert summary["periods"] == []
    assert summary["percentage"] is None


def test_days_before_the_start_date_are_not_missed():
    # created Oct 1 but set to start Nov 2: Nov 2 and 3 are the only finished days
    habit = make_habit(start_date="2026-11-02", completed_dates=days((11, 2), (11, 3)))

    summary = summary_for(habit, period_days=30)

    assert summary["starts_on"] is None
    assert (summary["completed"], summary["expected"], summary["percentage"]) == (2, 2, 100)
    assert summary["current_streak"] == 2


# breaks


def test_days_on_a_break_neither_break_nor_extend_a_streak():
    habit = make_habit(
        completed_dates=days((10, 28), (10, 29), (11, 3)),
        breaks=[{"start": "2026-10-30", "end": "2026-11-02"}],
    )

    summary = summary_for(habit)

    assert summary["current_streak"] == 3
    # Oct 29 - Nov 4: one done, four on a break, one done, and today in progress
    statuses = [period["status"] for period in summary["periods"]]
    assert statuses == ["met"] + ["paused"] * 4 + ["met", "in_progress"]


def test_days_on_a_break_do_not_lower_the_completion_rate():
    habit = make_habit(
        completed_dates=days((10, 29), (11, 3)),
        breaks=[{"start": "2026-10-30", "end": "2026-11-02"}],
    )

    summary = summary_for(habit, period_days=7)

    # Oct 29 - Nov 3 are finished, but Oct 30 - Nov 2 were on a break
    assert (summary["completed"], summary["expected"], summary["percentage"]) == (2, 2, 100)


def test_completion_during_a_break_still_counts():
    habit = make_habit(
        completed_dates=days((11, 1), (11, 2), (11, 3)),
        breaks=[{"start": "2026-11-02", "end": "2026-11-02"}],
    )

    summary = summary_for(habit)

    assert summary["current_streak"] == 3
    assert summary["periods"][-2]["status"] == "met"


def test_week_entirely_on_a_break_keeps_a_weekly_streak():
    habit = make_habit(
        frequency="weekly",
        target=2,
        # weeks of Oct 12 and Oct 26 hit the target; the week of Oct 19 was a break
        completed_dates=days((10, 13), (10, 15), (10, 27), (10, 29)),
        breaks=[{"start": "2026-10-19", "end": "2026-10-25"}],
    )

    summary = summary_for(habit)

    assert summary["current_streak"] == 2


def test_partial_break_lowers_a_weeks_target():
    # 5 of 7 days on a break leaves ceil(2 * 2 / 7) = 1 completion needed
    habit = make_habit(
        frequency="weekly",
        target=2,
        completed_dates=days(
            (10, 26),
        ),
        breaks=[{"start": "2026-10-27", "end": "2026-10-31"}],
    )

    periods = habit_periods(habit, TODAY)

    assert (periods[-2]["target"], periods[-2]["status"]) == (1, "met")


# not enough history


def test_new_habit_with_nothing_logged_has_no_history():
    habit = make_habit(created_at=datetime(2026, 11, 4, 9))

    assert not summary_for(habit)["has_history"]


def test_new_weekly_habit_has_no_history_until_the_week_is_over():
    habit = make_habit(frequency="weekly", created_at=datetime(2026, 11, 2))

    assert not summary_for(habit)["has_history"]
    assert summary_for(habit, today=date(2026, 11, 9))["has_history"]


def test_logging_a_new_habit_once_gives_it_history():
    habit = make_habit(created_at=datetime(2026, 11, 4, 9), completed_dates=days((11, 4)))

    summary = summary_for(habit)

    assert summary["has_history"]
    assert summary["current_streak"] == 1


# summary page


@pytest.fixture
def logged_in(client):
    with client.session_transaction() as session:
        session["user_id"] = "test-owner"
        session["user_email"] = OWNER_EMAIL


def utc_days_ago(*offsets):
    today = datetime.utcnow().date()
    return [(today - timedelta(days=offset)).isoformat() for offset in offsets]


def card_text(html, name):
    """Visible text of one habit's summary card, with whitespace collapsed."""
    for card in html.split("<article")[1:]:
        if name in card:
            card = card[: card.index("</article>")]
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card)).strip()
    raise AssertionError(f"no summary card for {name}")


def card_html(html, name):
    for card in html.split("<article")[1:]:
        if name in card:
            return card[: card.index("</article>")]
    raise AssertionError(f"no summary card for {name}")


@pytest.fixture
def logged_habit(habits, logged_in):
    habits.insert_one(
        make_habit(
            "Read",
            created_at=datetime.utcnow() - timedelta(days=10),
            completed_dates=utc_days_ago(1, 2, 3),
        )
    )


def test_summary_requires_login(client):
    response = client.get("/summary")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_dashboard_sidebar_links_to_summary_tab(client, logged_in):
    html = client.get("/dashboard").get_data(as_text=True)
    sidebar = html[html.index("<aside") : html.index("</aside>")]

    assert 'href="/summary"' in sidebar
    assert "cursor-not-allowed" not in sidebar


def test_summary_tab_shows_streaks_and_completion(client, logged_habit):
    response = client.get("/summary")
    text = card_text(response.get_data(as_text=True), "Read")

    assert response.status_code == 200
    assert "Current streak 3 days" in text
    assert "Longest streak 3 days" in text
    assert "Completion 50%" in text
    assert "3 of 6 completions in the last 7 days" in text


def test_summary_tab_draws_a_meter_and_history_strip(client, logged_habit):
    card = card_html(client.get("/summary").get_data(as_text=True), "Read")

    assert 'role="progressbar" aria-valuenow="50"' in card
    assert card.count("habit-history-cell") == 7
    assert card.count("habit-history-met") == 3
    assert card.count("habit-history-missed") == 3
    assert card.count("habit-history-in_progress") == 1
    assert "3 completed, 3 missed, 1 in progress" in re.sub(r"\s+", " ", card)


def test_summary_period_can_be_changed(client, logged_habit):
    html = client.get("/summary?period=30").get_data(as_text=True)
    text = card_text(html, "Read")

    assert '<option value="30" selected>' in re.sub(r"\s+", " ", html)
    assert "Completion 30%" in text
    assert "3 of 10 completions in the last 30 days" in text


@pytest.mark.parametrize("period", ["14", "abc", ""])
def test_unknown_period_falls_back_to_seven_days(client, logged_habit, period):
    text = card_text(client.get(f"/summary?period={period}").get_data(as_text=True), "Read")

    assert "in the last 7 days" in text


def test_habit_without_enough_history_says_so_and_shows_no_data(client, habits, logged_in):
    habits.insert_one(make_habit("Brand new", created_at=datetime.utcnow()))

    text = card_text(client.get("/summary").get_data(as_text=True), "Brand new")

    assert "Not enough history yet" in text
    assert "Current streak" not in text
    assert "%" not in text


def test_summary_only_shows_the_users_own_habits(client, habits, logged_habit):
    habits.insert_one(make_habit("Someone else's", user_email="another@example.com"))

    html = client.get("/summary").get_data(as_text=True)

    assert "Read" in html
    assert "Someone else&#39;s" not in html


def test_summary_without_habits_points_to_creating_one(client, logged_in):
    html = client.get("/summary").get_data(as_text=True)

    assert "No habits yet" in html
    assert 'href="/habit-tracker"' in html


def test_upcoming_habit_card_says_when_it_starts(client, habits, logged_in):
    start = (datetime.utcnow().date() + timedelta(days=5)).isoformat()
    habits.insert_one(make_habit("Later", created_at=datetime.utcnow(), start_date=start))

    text = card_text(client.get("/summary").get_data(as_text=True), "Later")

    assert "Not enough history yet" in text
    assert "This habit starts on" in text
    assert "Current streak" not in text


def test_break_days_show_as_on_break_in_the_history_strip(client, habits, logged_in):
    today = datetime.utcnow().date()
    habits.insert_one(
        make_habit(
            "Rest",
            created_at=datetime.utcnow() - timedelta(days=10),
            completed_dates=utc_days_ago(1, 5, 6),
            breaks=[
                {
                    "start": (today - timedelta(days=4)).isoformat(),
                    "end": (today - timedelta(days=2)).isoformat(),
                }
            ],
        )
    )

    html = client.get("/summary").get_data(as_text=True)
    card = card_html(html, "Rest")

    assert card.count("habit-history-paused") == 3
    assert "Current streak 3 days" in card_text(html, "Rest")
    assert "Completion 100%" in card_text(html, "Rest")
    assert "3 on break" in re.sub(r"\s+", " ", card)
