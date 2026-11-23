"""Tests for gradually increasing daily goals (US-39)."""

from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
import csv
import io
import json

from app import _parse_goal_settings, _parse_habit_file
from app import is_overdue
from reminders import _goal_met
from app import _target_edit_updates

from progress import (
    build_habit_history,
    calculate_overall_progress,
    current_period_progress,
    goal_increase_suggestion,
    habit_periods,
    habit_summary,
    target_on,
)
from tests.conftest import authenticated_client, habits

TODAY = date(2026, 11, 22)


def make_habit(**changes):
    habit = {
        "name": "Read",
        "frequency": "daily",
        "target": 1,
        "status": "active",
        "start_date": "2026-11-01",
        "created_at": datetime(2026, 11, 1, 12, 0),
        "completed_dates": [],
        "completion_history": [],
        "skipped_dates": [],
        "breaks": [],
    }
    habit.update(changes)
    return habit


def habit_with_scheduled_increase():
    completed_dates = [
        (TODAY - timedelta(days=offset)).isoformat()
        for offset in range(7)
    ]
    return make_habit(
        target=2,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-23", "target": 2}
        ],
        completed_dates=completed_dates,
    )


@pytest.mark.parametrize("target", [1, 2, 5])
def test_legacy_habit_uses_existing_target(target):
    habit = make_habit(target=target)

    assert target_on(habit, TODAY) == target
    assert current_period_progress(habit, TODAY)["target"] == target


@pytest.mark.parametrize(
    "on_date, expected",
    [
        (date(2026, 11, 21), 1),
        (date(2026, 11, 22), 1),
        (date(2026, 11, 23), 2),
        (date(2026, 11, 24), 2),
    ],
)
def test_target_changes_on_effective_date(on_date, expected):
    habit = habit_with_scheduled_increase()

    assert target_on(habit, on_date) == expected


def test_target_history_supports_multiple_changes():
    habit = make_habit(
        target=3,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-23", "target": 2},
            {"effective_date": "2026-12-01", "target": 3},
        ],
    )

    assert target_on(habit, date(2026, 11, 22)) == 1
    assert target_on(habit, date(2026, 11, 23)) == 2
    assert target_on(habit, date(2026, 11, 30)) == 2
    assert target_on(habit, date(2026, 12, 1)) == 3


def test_target_history_does_not_depend_on_storage_order():
    habit = make_habit(
        target=3,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-12-01", "target": 3},
            {"effective_date": "2026-11-23", "target": 2},
        ],
    )

    assert target_on(habit, date(2026, 11, 24)) == 2
    assert target_on(habit, date(2026, 12, 1)) == 3


def test_target_lookup_does_not_modify_habit():
    habit = habit_with_scheduled_increase()
    before = deepcopy(habit)

    target_on(habit, TODAY)

    assert habit == before


def test_scheduled_increase_keeps_today_complete():
    habit = habit_with_scheduled_increase()

    progress = current_period_progress(habit, TODAY)

    assert progress["target"] == 1
    assert progress["completed"] == 1
    assert progress["status"] == "Complete"


def test_higher_target_applies_tomorrow():
    habit = habit_with_scheduled_increase()
    habit["completed_dates"].append("2026-11-23")

    progress = current_period_progress(habit, date(2026, 11, 23))

    assert progress["target"] == 2
    assert progress["completed"] == 1
    assert progress["status"] == "In Progress"


def test_scheduled_increase_preserves_past_successful_periods():
    habit = habit_with_scheduled_increase()

    periods = habit_periods(habit, TODAY)
    recent = [
        period
        for period in periods
        if period["start"] >= TODAY - timedelta(days=6)
    ]

    assert len(recent) == 7
    assert all(period["target"] == 1 for period in recent)
    assert all(period["status"] == "met" for period in recent)


def test_scheduled_increase_preserves_overall_progress():
    habit = habit_with_scheduled_increase()

    progress = calculate_overall_progress(
        [habit], end_date=TODAY, period_days=7
    )

    assert progress["scheduled"] == 7
    assert progress["completed"] == 7
    assert progress["percentage"] == 100


def test_overall_progress_uses_each_dates_target():
    habit = habit_with_scheduled_increase()
    habit["completed_dates"].append("2026-11-23")

    progress = calculate_overall_progress(
        [habit],
        end_date=date(2026, 11, 23),
        period_days=7,
    )

    assert progress["scheduled"] == 8
    assert progress["completed"] == 7


def test_scheduled_increase_preserves_summary_streak():
    habit = habit_with_scheduled_increase()

    summary = habit_summary(habit, TODAY, period_days=7)

    assert summary["current_streak"] == 7
    assert summary["longest_streak"] == 7
    assert summary["percentage"] == 100


def test_history_retains_original_daily_target():
    habit = habit_with_scheduled_increase()

    history = build_habit_history(habit, TODAY)
    recent = [
        entry
        for entry in history
        if entry["start"] >= TODAY - timedelta(days=6)
    ]

    assert len(recent) == 7
    assert all(entry["target"] == 1 for entry in recent)
    assert all(entry["status"] == "Completed" for entry in recent)


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_non_daily_habits_keep_existing_target_behavior(frequency):
    habit = make_habit(
        frequency=frequency,
        target=4,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-23", "target": 2}
        ],
    )

    assert target_on(habit, TODAY) == 4
    assert target_on(habit, date(2026, 11, 23)) == 4
    assert current_period_progress(habit, TODAY)["target"] == 4

def successful_daily_habit(target=1, **changes):
    records = [
        {
            "date": (TODAY - timedelta(days=offset)).isoformat(),
            "status": "completed",
            "count": target,
        }
        for offset in range(1, 8)
    ]
    habit = make_habit(
        target=target,
        completion_history=records,
    )
    habit.update(changes)
    return habit


def test_suggests_increase_after_seven_successful_days():
    habit = successful_daily_habit()

    suggestion = goal_increase_suggestion(habit, TODAY)

    assert suggestion == {
        "current_target": 1,
        "suggested_target": 2,
        "effective_date": "2026-11-23",
        "window_start": "2026-11-15",
        "window_end": "2026-11-21",
    }


def test_suggestion_uses_count_based_target():
    habit = successful_daily_habit(target=3)

    suggestion = goal_increase_suggestion(habit, TODAY)

    assert suggestion["current_target"] == 3
    assert suggestion["suggested_target"] == 4


def test_suggestion_supports_legacy_completed_dates():
    habit = successful_daily_habit()
    habit["completed_dates"] = [
        record["date"] for record in habit["completion_history"]
    ]
    habit.pop("completion_history")

    assert goal_increase_suggestion(habit, TODAY) is not None


def test_six_successful_days_are_not_enough():
    habit = successful_daily_habit()
    habit["completion_history"].pop()

    assert goal_increase_suggestion(habit, TODAY) is None


def test_today_does_not_replace_a_missing_prior_day():
    habit = successful_daily_habit()
    habit["completion_history"].pop()
    habit["completion_history"].append(
        {"date": TODAY.isoformat(), "status": "completed", "count": 1}
    )

    assert goal_increase_suggestion(habit, TODAY) is None


def test_partial_day_prevents_suggestion():
    habit = successful_daily_habit(target=2)
    habit["completion_history"][3]["count"] = 1

    assert goal_increase_suggestion(habit, TODAY) is None


@pytest.mark.parametrize("status", ["missed", "skipped", "undone"])
def test_unsuccessful_history_status_prevents_suggestion(status):
    habit = successful_daily_habit()
    habit["completion_history"][3]["status"] = status

    assert goal_increase_suggestion(habit, TODAY) is None


def test_break_in_success_window_prevents_suggestion():
    habit = successful_daily_habit(
        breaks=[{"start": "2026-11-18", "end": "2026-11-18"}]
    )

    assert goal_increase_suggestion(habit, TODAY) is None


def test_break_today_hides_suggestion():
    habit = successful_daily_habit(
        breaks=[{"start": "2026-11-22", "end": "2026-11-24"}]
    )

    assert goal_increase_suggestion(habit, TODAY) is None


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_non_daily_habit_has_no_suggestion(frequency):
    habit = successful_daily_habit(frequency=frequency)

    assert goal_increase_suggestion(habit, TODAY) is None


@pytest.mark.parametrize("status", ["paused", "archived", "expired"])
def test_inactive_habit_has_no_suggestion(status):
    habit = successful_daily_habit(status=status)

    assert goal_increase_suggestion(habit, TODAY) is None


def test_days_before_start_cannot_qualify():
    habit = successful_daily_habit(start_date="2026-11-16")

    assert goal_increase_suggestion(habit, TODAY) is None


def test_start_on_first_qualifying_day_is_allowed():
    habit = successful_daily_habit(start_date="2026-11-15")

    assert goal_increase_suggestion(habit, TODAY) is not None


def test_pending_target_increase_prevents_another_suggestion():
    habit = successful_daily_habit(
        target=2,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-23", "target": 2}
        ],
    )

    assert goal_increase_suggestion(habit, TODAY) is None


def test_old_lower_target_days_cannot_trigger_next_increase():
    habit = successful_daily_habit(
        target=2,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-20", "target": 2}
        ],
    )
    for record in habit["completion_history"]:
        record["count"] = 2

    assert goal_increase_suggestion(habit, TODAY) is None


def test_seven_days_at_new_target_can_qualify():
    habit = successful_daily_habit(
        target=2,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-15", "target": 2}
        ],
    )

    assert goal_increase_suggestion(habit, TODAY) is not None


@pytest.mark.parametrize(
    "decision_date, eligible",
    [
        ("2026-11-14", True),
        ("2026-11-15", False),
        ("2026-11-21", False),
        ("2026-11-22", False),
    ],
)
def test_decision_requires_seven_new_successful_days(
    decision_date, eligible
):
    habit = successful_daily_habit(
        goal_suggestion_last_decision=decision_date
    )

    suggestion = goal_increase_suggestion(habit, TODAY)

    assert (suggestion is not None) is eligible


def test_suggestion_calculation_does_not_modify_habit():
    habit = successful_daily_habit()
    before = deepcopy(habit)

    goal_increase_suggestion(habit, TODAY)

    assert habit == before


def test_reminder_uses_old_target_before_effective_date():
    habit = habit_with_scheduled_increase()

    assert _goal_met(habit, TODAY) is True


def test_reminder_requires_higher_target_on_effective_date():
    habit = habit_with_scheduled_increase()
    habit["completion_history"].append(
        {
            "date": "2026-11-23",
            "status": "completed",
            "count": 1,
        }
    )

    assert _goal_met(habit, date(2026, 11, 23)) is False


def test_reminder_stops_when_higher_target_is_met():
    habit = habit_with_scheduled_increase()
    habit["completion_history"].append(
        {
            "date": "2026-11-23",
            "status": "completed",
            "count": 2,
        }
    )

    assert _goal_met(habit, date(2026, 11, 23)) is True


def test_overdue_uses_yesterdays_historical_target():
    habit = make_habit(
        target=2,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-22", "target": 2}
        ],
        when_missed="overdue",
        completed_dates=["2026-11-21"],
    )

    assert is_overdue(habit, TODAY) is False


def test_daily_partial_yesterday_is_overdue():
    habit = make_habit(
        target=2,
        when_missed="overdue",
        completion_history=[
            {
                "date": "2026-11-21",
                "status": "completed",
                "count": 1,
            }
        ],
    )

    assert is_overdue(habit, TODAY) is True


def test_partial_today_does_not_clear_overdue():
    habit = make_habit(
        target=2,
        when_missed="overdue",
        completion_history=[
            {
                "date": "2026-11-22",
                "status": "completed",
                "count": 1,
            }
        ],
    )

    assert is_overdue(habit, TODAY) is True


def test_meeting_today_target_clears_overdue():
    habit = make_habit(
        target=2,
        when_missed="overdue",
        completion_history=[
            {
                "date": "2026-11-22",
                "status": "completed",
                "count": 2,
            }
        ],
    )

    assert is_overdue(habit, TODAY) is False


def test_undone_yesterday_is_not_a_success_from_legacy_date():
    habit = make_habit(
        target=1,
        when_missed="overdue",
        completed_dates=["2026-11-21"],
        completion_history=[
            {
                "date": "2026-11-21",
                "status": "undone",
                "count": 0,
            }
        ],
    )

    assert is_overdue(habit, TODAY) is True

def test_progressive_goal_export_preserves_metadata(
    authenticated_client, habits
):
    habit = habit_with_scheduled_increase()
    habit["user_email"] = "person@example.com"
    habit["goal_suggestion_last_decision"] = "2026-11-22"
    habit_id = habits.insert_one(habit).inserted_id

    response = authenticated_client.get(f"/habits/{habit_id}/export")

    assert response.status_code == 200
    restored = _parse_habit_file(response.data)

    assert restored["target"] == 2
    assert restored["initial_target"] == 1
    assert restored["target_history"] == habit["target_history"]
    assert restored["start_date"] == habit["start_date"]
    assert restored["goal_suggestion_last_decision"] == "2026-11-22"
    assert current_period_progress(restored, TODAY)["target"] == 1
    assert habit_summary(restored, TODAY)["current_streak"] == 7


def test_progressive_goal_backup_preserves_multiple_checkins(
    authenticated_client, habits
):
    habit = make_habit(
        user_email="person@example.com",
        target=3,
        initial_target=2,
        target_history=[
            {"effective_date": "2026-11-23", "target": 3}
        ],
        completed_dates=["2026-11-21"],
        completion_history=[
            {"date": "2026-11-21", "status": "completed", "count": 2}
        ],
    )
    habit_id = habits.insert_one(habit).inserted_id

    response = authenticated_client.get(f"/habits/{habit_id}/export")

    assert response.status_code == 200
    restored = _parse_habit_file(response.data)
    progress = current_period_progress(restored, date(2026, 11, 21))

    assert progress["target"] == 2
    assert progress["completed"] == 2
    assert progress["status"] == "Complete"


def test_ordinary_export_keeps_legacy_columns(
    authenticated_client, habits
):
    from models import HABIT_FILE_FIELDS

    habit = make_habit(user_email="person@example.com")
    habit_id = habits.insert_one(habit).inserted_id

    response = authenticated_client.get(f"/habits/{habit_id}/export")

    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    assert reader.fieldnames == HABIT_FILE_FIELDS


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"initial_target": True, "target_history": []},
        {
            "initial_target": 1,
            "target_history": [
                {"effective_date": "2026-11-23", "target": 0}
            ],
        },
        {
            "initial_target": 1,
            "target_history": [
                {"effective_date": "20261123", "target": 2}
            ],
        },
        {
            "initial_target": 1,
            "target_history": [
                {"effective_date": "2026-11-23", "target": 2},
                {"effective_date": "2026-11-23", "target": 2},
            ],
        },
        {
            "initial_target": 1,
            "target_history": [
                {"effective_date": "2026-11-23", "target": 3}
            ],
        },
        {
            "initial_target": 1,
            "target_history": [
                {"effective_date": "2026-11-23", "target": 2}
            ],
            "user_email": "someone-else@example.com",
        },
    ],
)
def test_invalid_goal_backup_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        _parse_goal_settings(json.dumps(settings), 2, "daily")


def test_goal_backup_settings_reject_non_daily_habit():
    settings = {
        "initial_target": 1,
        "target_history": [
            {"effective_date": "2026-11-23", "target": 2}
        ],
    }

    with pytest.raises(ValueError):
        _parse_goal_settings(json.dumps(settings), 2, "weekly")


def test_goal_backup_settings_reject_invalid_json():
    with pytest.raises(ValueError):
        _parse_goal_settings("not-json", 2, "daily")

def test_manual_daily_target_edit_starts_tomorrow():
    habit = make_habit(target=1)
    before = deepcopy(habit)

    updates = _target_edit_updates(habit, 2, "daily", TODAY)
    saved = {**habit, "target": 2, **updates}

    assert habit == before
    assert saved["initial_target"] == 1
    assert target_on(saved, TODAY - timedelta(days=1)) == 1
    assert target_on(saved, TODAY) == 1
    assert target_on(saved, TODAY + timedelta(days=1)) == 2
    assert updates["goal_suggestion_last_decision"] == TODAY.isoformat()


def test_manual_target_decrease_also_starts_tomorrow():
    habit = make_habit(target=3)

    updates = _target_edit_updates(habit, 1, "daily", TODAY)
    saved = {**habit, "target": 1, **updates}

    assert target_on(saved, TODAY) == 3
    assert target_on(saved, TODAY + timedelta(days=1)) == 1


def test_unchanged_target_preserves_pending_increase():
    habit = habit_with_scheduled_increase()
    before = deepcopy(habit)

    updates = _target_edit_updates(habit, 2, "daily", TODAY)

    assert updates == {}
    assert habit == before


def test_manual_edit_replaces_pending_increase():
    habit = habit_with_scheduled_increase()

    updates = _target_edit_updates(habit, 3, "daily", TODAY)
    saved = {**habit, "target": 3, **updates}

    assert saved["target_history"] == [
        {"effective_date": "2026-11-23", "target": 3}
    ]
    assert target_on(saved, TODAY) == 1
    assert target_on(saved, TODAY + timedelta(days=1)) == 3


def test_setting_target_back_to_today_cancels_pending_increase():
    habit = habit_with_scheduled_increase()

    updates = _target_edit_updates(habit, 1, "daily", TODAY)
    saved = {**habit, "target": 1, **updates}

    assert saved["target_history"] == []
    assert target_on(saved, TODAY) == 1
    assert target_on(saved, TODAY + timedelta(days=1)) == 1


def test_manual_edit_preserves_previous_effective_changes():
    habit = make_habit(
        target=3,
        initial_target=1,
        target_history=[
            {"effective_date": "2026-11-20", "target": 2},
            {"effective_date": "2026-11-23", "target": 3},
        ],
    )

    updates = _target_edit_updates(habit, 4, "daily", TODAY)
    saved = {**habit, "target": 4, **updates}

    assert saved["target_history"] == [
        {"effective_date": "2026-11-20", "target": 2},
        {"effective_date": "2026-11-23", "target": 4},
    ]
    assert target_on(saved, date(2026, 11, 19)) == 1
    assert target_on(saved, TODAY) == 2
    assert target_on(saved, TODAY + timedelta(days=1)) == 4


def test_manual_edit_keeps_today_complete():
    habit = make_habit(completed_dates=[TODAY.isoformat()])

    updates = _target_edit_updates(habit, 2, "daily", TODAY)
    saved = {**habit, "target": 2, **updates}

    assert current_period_progress(saved, TODAY)["status"] == "Complete"


def test_manual_edit_starts_new_suggestion_window():
    habit = make_habit(
        completed_dates=[
            (TODAY - timedelta(days=offset)).isoformat()
            for offset in range(1, 8)
        ],
    )

    updates = _target_edit_updates(habit, 2, "daily", TODAY)
    saved = {**habit, "target": 2, **updates}

    assert goal_increase_suggestion(saved, TODAY) is None


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_non_daily_target_edit_keeps_existing_behavior(frequency):
    habit = make_habit(frequency=frequency, target=1)

    assert _target_edit_updates(habit, 3, frequency, TODAY) == {}


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_frequency_change_rejected_for_dated_target_habit(frequency):
    habit = habit_with_scheduled_increase()

    with pytest.raises(ValueError, match="Frequency cannot be changed"):
        _target_edit_updates(habit, 2, frequency, TODAY)


def test_legacy_frequency_change_keeps_existing_behavior():
    habit = make_habit()

    assert _target_edit_updates(habit, 2, "weekly", TODAY) == {}

@pytest.fixture
def suggestion_route_habit(authenticated_client, habits, monkeypatch):
    monkeypatch.setattr("app.current_app_date", lambda: TODAY)

    habit = make_habit(
        user_email="person@example.com",
        completed_dates=[
            (TODAY - timedelta(days=offset)).isoformat()
            for offset in range(1, 8)
        ],
    )
    return habits.insert_one(habit).inserted_id


def post_goal_decision(client, habit_id, decision, **changes):
    response = client.get(f"/habits/{habit_id}/edit")
    assert response.status_code == 200

    with client.session_transaction() as session:
        token = session["habit_form_token"]

    return client.post(
        f"/habits/{habit_id}/goal-increase/{decision}",
        data={
            "csrf_token": token,
            "current_target": "1",
            "effective_date": "2026-11-23",
            **changes,
        },
    )


def test_accept_suggestion_schedules_tomorrow_and_preserves_history(
    authenticated_client, habits, suggestion_route_habit
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    response = post_goal_decision(
        authenticated_client, habit_id, "accept"
    )

    assert response.status_code == 302
    saved = habits.find_one({"_id": habit_id})

    assert saved["target"] == 2
    assert saved["initial_target"] == 1
    assert saved["target_history"] == [
        {"effective_date": "2026-11-23", "target": 2}
    ]
    assert saved["goal_suggestion_last_decision"] == "2026-11-22"
    assert target_on(saved, TODAY) == 1
    assert target_on(saved, date(2026, 11, 23)) == 2
    assert saved["completed_dates"] == original["completed_dates"]
    assert saved["completion_history"] == original["completion_history"]
    assert saved["user_email"] == original["user_email"]
    assert habit_summary(saved, TODAY)["current_streak"] == 7
    assert goal_increase_suggestion(saved, TODAY) is None


def test_dismiss_suggestion_keeps_target_and_history(
    authenticated_client, habits, suggestion_route_habit
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    response = post_goal_decision(
        authenticated_client, habit_id, "dismiss"
    )

    assert response.status_code == 302
    saved = habits.find_one({"_id": habit_id})

    assert saved["goal_suggestion_last_decision"] == "2026-11-22"
    assert goal_increase_suggestion(saved, TODAY) is None

    saved.pop("goal_suggestion_last_decision")
    assert saved == original


@pytest.mark.parametrize("first_decision", ["accept", "dismiss"])
@pytest.mark.parametrize("second_decision", ["accept", "dismiss"])
def test_repeated_goal_decision_does_not_write_again(
    authenticated_client,
    habits,
    suggestion_route_habit,
    first_decision,
    second_decision,
):
    habit_id = suggestion_route_habit

    first = post_goal_decision(
        authenticated_client, habit_id, first_decision
    )
    assert first.status_code == 302
    after_first = habits.find_one({"_id": habit_id})

    second = post_goal_decision(
        authenticated_client, habit_id, second_decision
    )

    assert second.status_code == 409
    assert habits.find_one({"_id": habit_id}) == after_first


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_goal_decision_rechecks_eligibility(
    authenticated_client, habits, suggestion_route_habit, decision
):
    habit_id = suggestion_route_habit
    habits.update_one(
        {"_id": habit_id},
        {"$set": {"completed_dates": []}},
    )
    original = habits.find_one({"_id": habit_id})

    response = post_goal_decision(
        authenticated_client, habit_id, decision
    )

    assert response.status_code == 409
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize(
    "changes",
    [
        {"current_target": "99"},
        {"effective_date": "2026-11-24"},
        {"effective_date": ""},
    ],
)
def test_stale_goal_suggestion_is_rejected(
    authenticated_client, habits, suggestion_route_habit, changes
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    response = post_goal_decision(
        authenticated_client, habit_id, "accept", **changes
    )

    assert response.status_code == 409
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_goal_decision_requires_csrf(
    authenticated_client, habits, suggestion_route_habit, decision
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    response = authenticated_client.post(
        f"/habits/{habit_id}/goal-increase/{decision}",
        data={
            "current_target": "1",
            "effective_date": "2026-11-23",
        },
    )

    assert response.status_code == 400
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_goal_decision_requires_login(
    authenticated_client, habits, suggestion_route_habit, decision
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    with authenticated_client.session_transaction() as session:
        session.clear()

    response = authenticated_client.post(
        f"/habits/{habit_id}/goal-increase/{decision}"
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_goal_decision_cannot_modify_another_users_habit(
    authenticated_client, habits, suggestion_route_habit, decision
):
    habit_id = suggestion_route_habit
    habits.update_one(
        {"_id": habit_id},
        {"$set": {"user_email": "someone-else@example.com"}},
    )
    original = habits.find_one({"_id": habit_id})

    response = authenticated_client.post(
        f"/habits/{habit_id}/goal-increase/{decision}"
    )

    assert response.status_code == 404
    assert habits.find_one({"_id": habit_id}) == original


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_goal_decision_does_not_allow_get(
    authenticated_client, habits, suggestion_route_habit, decision
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    response = authenticated_client.get(
        f"/habits/{habit_id}/goal-increase/{decision}"
    )

    assert response.status_code == 405
    assert habits.find_one({"_id": habit_id}) == original 

@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_goal_decision_rejects_concurrent_completion_change(
    authenticated_client,
    habits,
    suggestion_route_habit,
    monkeypatch,
    decision,
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})
    original_update_one = habits.update_one
    race = {"triggered": False}

    def update_after_completion_change(query, update, *args, **kwargs):
        if "$and" in query and not race["triggered"]:
            race["triggered"] = True
            original_update_one(
                {"_id": habit_id},
                {"$set": {"completed_dates": []}},
            )

        return original_update_one(query, update, *args, **kwargs)

    monkeypatch.setattr(
        habits,
        "update_one",
        update_after_completion_change,
    )

    response = post_goal_decision(
        authenticated_client,
        habit_id,
        decision,
    )

    assert race["triggered"] is True
    assert response.status_code == 409

    saved = habits.find_one({"_id": habit_id})
    expected = {**original, "completed_dates": []}
    assert saved == expected

def test_dashboard_shows_eligible_goal_suggestion(
    authenticated_client, habits, suggestion_route_habit
):
    habit_id = suggestion_route_habit
    original = habits.find_one({"_id": habit_id})

    response = authenticated_client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'data-goal-suggestion="{habit_id}"' in html
    assert "Ready for a small increase?" in html
    assert "Accept increase" in html
    assert "Not now" in html
    assert 'name="current_target"' in html
    assert 'name="effective_date"' in html
    assert 'value="2026-11-23"' in html
    assert (
        f'action="/habits/{habit_id}/goal-increase/accept"'
        in html
    )
    assert (
        f'action="/habits/{habit_id}/goal-increase/dismiss"'
        in html
    )

    # Viewing a suggestion must never change the habit.
    assert habits.find_one({"_id": habit_id}) == original


def test_dashboard_hides_ineligible_goal_suggestion(
    authenticated_client, habits, suggestion_route_habit
):
    habit_id = suggestion_route_habit
    habits.update_one(
        {"_id": habit_id},
        {"$set": {"completed_dates": []}},
    )

    response = authenticated_client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'data-goal-suggestion="{habit_id}"' not in html
    assert "Ready for a small increase?" not in html


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_dashboard_hides_suggestion_after_decision(
    authenticated_client, suggestion_route_habit, decision
):
    habit_id = suggestion_route_habit
    response = post_goal_decision(
        authenticated_client, habit_id, decision
    )
    assert response.status_code == 302

    dashboard = authenticated_client.get("/dashboard")
    html = dashboard.get_data(as_text=True)

    assert dashboard.status_code == 200
    assert f'data-goal-suggestion="{habit_id}"' not in html


def test_dashboard_does_not_show_another_users_suggestion(
    authenticated_client, habits, suggestion_route_habit
):
    habit_id = suggestion_route_habit
    habits.update_one(
        {"_id": habit_id},
        {
            "$set": {
                "user_email": "someone-else@example.com",
                "name": "Private eligible habit",
            }
        },
    )

    response = authenticated_client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'data-goal-suggestion="{habit_id}"' not in html
    assert "Private eligible habit" not in html    
def test_dismissed_suggestion_backup_preserves_decision(
    authenticated_client, habits, suggestion_route_habit
):
    habit_id = suggestion_route_habit
    response = post_goal_decision(
        authenticated_client, habit_id, "dismiss"
    )
    assert response.status_code == 302

    exported = authenticated_client.get(f"/habits/{habit_id}/export")
    assert exported.status_code == 200
    restored = _parse_habit_file(exported.data)

    assert restored["target"] == 1
    assert restored["initial_target"] == 1
    assert restored["target_history"] == []
    assert restored["goal_suggestion_last_decision"] == TODAY.isoformat()
    assert goal_increase_suggestion(restored, TODAY) is None


def test_cancelled_first_increase_backup_preserves_metadata(
    authenticated_client, habits, monkeypatch
):
    monkeypatch.setattr("app.current_app_date", lambda: TODAY)
    habit = habit_with_scheduled_increase()

    updates = _target_edit_updates(habit, 1, "daily", TODAY)
    habit.update({"target": 1, **updates})
    habit["user_email"] = "person@example.com"
    habit_id = habits.insert_one(habit).inserted_id

    exported = authenticated_client.get(f"/habits/{habit_id}/export")
    assert exported.status_code == 200
    restored = _parse_habit_file(exported.data)

    assert restored["initial_target"] == 1
    assert restored["target_history"] == []
    assert restored["start_date"] == habit["start_date"]
    assert restored["goal_suggestion_last_decision"] == TODAY.isoformat()
    assert target_on(restored, TODAY) == 1
    assert target_on(restored, TODAY + timedelta(days=1)) == 1


def test_edit_rejects_target_change_during_request(
    authenticated_client, habits, suggestion_route_habit, monkeypatch
):
    habit_id = suggestion_route_habit
    response = authenticated_client.get(f"/habits/{habit_id}/edit")
    assert response.status_code == 200

    with authenticated_client.session_transaction() as session:
        token = session["habit_form_token"]

    original = habits.find_one({"_id": habit_id})
    original_update_one = habits.update_one
    race = {"triggered": False}
    concurrent_change = {
        "target": 2,
        "initial_target": 1,
        "target_history": [
            {"effective_date": "2026-11-23", "target": 2}
        ],
        "goal_suggestion_last_decision": TODAY.isoformat(),
    }

    def update_after_target_change(query, update, *args, **kwargs):
        if not race["triggered"]:
            race["triggered"] = True
            original_update_one(
                {"_id": habit_id},
                {"$set": concurrent_change},
            )

        return original_update_one(query, update, *args, **kwargs)

    monkeypatch.setattr(habits, "update_one", update_after_target_change)

    response = authenticated_client.post(
        f"/habits/{habit_id}/edit",
        data={
            "name": "Read",
            "description": "Should not be saved",
            "target": "3",
            "frequency": "daily",
            "reminder_timezone": "UTC",
            "when_missed": "fail",
            "csrf_token": token,
        },
    )

    assert race["triggered"] is True
    assert response.status_code == 409
    assert habits.find_one({"_id": habit_id}) == {
        **original,
        **concurrent_change,
    }    
def test_history_header_uses_effective_target_not_pending_target(
    authenticated_client, habits, monkeypatch
):
    monkeypatch.setattr("app.current_app_date", lambda: TODAY)
    habit = habit_with_scheduled_increase()
    habit["user_email"] = "person@example.com"
    habit_id = habits.insert_one(habit).inserted_id

    response = authenticated_client.get(f"/habits/{habit_id}/history")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Current target: 1" in html
    assert "Current target: 2" not in html

    monkeypatch.setattr(
        "app.current_app_date",
        lambda: TODAY + timedelta(days=1),
    )

    tomorrow_response = authenticated_client.get(
        f"/habits/{habit_id}/history"
    )
    tomorrow_html = tomorrow_response.get_data(as_text=True)

    assert tomorrow_response.status_code == 200
    assert "Current target: 2" in tomorrow_html    