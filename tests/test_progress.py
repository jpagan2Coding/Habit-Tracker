from datetime import date, datetime

import pytest

from progress import calculate_overall_progress


def habit(frequency="daily", target=1, created_at=datetime(2026, 10, 1), **values):
    return {
        "frequency": frequency,
        "target": target,
        "created_at": created_at,
        **values,
    }


def test_daily_progress_uses_scheduled_occurrences_and_completions():
    result = calculate_overall_progress(
        [habit(completed_dates=["2026-10-01", "2026-10-03"])],
        end_date=date(2026, 10, 7),
        period_days=7,
    )
    assert result["scheduled"] == 7
    assert result["completed"] == 2
    assert result["percentage"] == 29


def test_weekly_and_monthly_schedules_are_respected():
    weekly = habit("weekly", created_at=datetime(2026, 9, 1))
    monthly = habit("monthly", created_at=datetime(2026, 9, 30))
    result = calculate_overall_progress(
        [weekly, monthly], end_date=date(2026, 10, 31), period_days=30
    )
    assert result["scheduled"] == 5


@pytest.mark.parametrize("period", [7, 30, 90])
def test_supported_date_ranges(period):
    result = calculate_overall_progress([], end_date=date(2026, 10, 7), period_days=period)
    assert result["scheduled"] == 0
    assert result["percentage"] == 0


def test_status_changes_are_reflected():
    result = calculate_overall_progress(
        [
            habit(
                completion_history=[
                    {"date": "2026-10-01", "status": "completed"},
                    {"date": "2026-10-02", "status": "skipped"},
                    {"date": "2026-10-03", "status": "missed"},
                    {"date": "2026-10-04", "status": "completed"},
                    {"date": "2026-10-04", "status": "undone"},
                ]
            )
        ],
        end_date=date(2026, 10, 7),
        period_days=7,
    )
    assert result["scheduled"] == 7
    assert result["completed"] == 1


def test_habits_created_after_period_are_not_scheduled():
    result = calculate_overall_progress(
        [habit(created_at=datetime(2026, 10, 8))],
        end_date=date(2026, 10, 7),
        period_days=7,
    )
    assert result["scheduled"] == 0
