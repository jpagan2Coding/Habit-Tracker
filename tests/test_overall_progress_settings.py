import re

import pytest

from progress import VALID_PROGRESS_PERIODS


def _progress_settings_page(client):
    return client.get("/settings").get_data(as_text=True)


def _progress_dashboard_page(client):
    return client.get("/dashboard").get_data(as_text=True)


def _is_checked(html, name):
    return re.search(
        rf'<input(?=[^>]*\bname="{re.escape(name)}")(?=[^>]*\bchecked\b)[^>]*>',
        html,
    ) is not None


def _is_selected(html, name, value):
    select = re.search(
        rf'<select(?=[^>]*\bname="{re.escape(name)}")[^>]*>(.*?)</select>',
        html,
        re.DOTALL,
    )
    assert select is not None
    return re.search(
        rf'<option(?=[^>]*\bvalue="{re.escape(str(value))}")(?=[^>]*\bselected\b)[^>]*>',
        select.group(1),
    ) is not None


def _save_progress_settings(client, *, enabled, days):
    data = {
        "overall_progress_days": str(days),
        "text_size": "default",
    }
    if enabled:
        data["overall_progress_enabled"] = "on"
    return client.post("/settings", data=data)


def test_past_n_day_progress_is_off_by_default(authenticated_client):
    settings_html = _progress_settings_page(authenticated_client)
    dashboard_html = _progress_dashboard_page(authenticated_client)

    assert not _is_checked(settings_html, "overall_progress_enabled")
    assert "Overall Progress" not in dashboard_html


def test_progress_toggle_and_period_are_only_configurable_in_settings(
    authenticated_client,
):
    settings_html = _progress_settings_page(authenticated_client)
    dashboard_html = _progress_dashboard_page(authenticated_client)

    assert 'name="overall_progress_enabled"' in settings_html
    assert 'name="overall_progress_days"' in settings_html
    assert 'name="overall_progress_enabled"' not in dashboard_html
    assert 'name="overall_progress_days"' not in dashboard_html
    assert 'name="period"' not in dashboard_html


def test_settings_offer_supported_periods_and_default_to_seven_days(
    authenticated_client,
):
    settings_html = _progress_settings_page(authenticated_client)

    for period in VALID_PROGRESS_PERIODS:
        assert re.search(rf'<option[^>]*\bvalue="{period}"', settings_html)
    assert _is_selected(settings_html, "overall_progress_days", 7)


@pytest.mark.parametrize("days", VALID_PROGRESS_PERIODS)
def test_enabling_progress_in_settings_persists_toggle_and_period(
    authenticated_client, days
):
    response = _save_progress_settings(authenticated_client, enabled=True, days=days)

    assert response.status_code == 302
    settings_html = _progress_settings_page(authenticated_client)
    dashboard_html = _progress_dashboard_page(authenticated_client)

    assert _is_checked(settings_html, "overall_progress_enabled")
    assert _is_selected(settings_html, "overall_progress_days", days)
    assert "Overall Progress" in dashboard_html
    assert f"Last {days} days" in dashboard_html


def test_disabling_progress_in_settings_hides_it_without_losing_period(
    authenticated_client,
):
    _save_progress_settings(authenticated_client, enabled=True, days=30)

    response = _save_progress_settings(authenticated_client, enabled=False, days=30)

    assert response.status_code == 302
    settings_html = _progress_settings_page(authenticated_client)
    dashboard_html = _progress_dashboard_page(authenticated_client)

    assert not _is_checked(settings_html, "overall_progress_enabled")
    assert _is_selected(settings_html, "overall_progress_days", 30)
    assert "Overall Progress" not in dashboard_html
