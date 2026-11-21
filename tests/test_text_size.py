import pytest

from app import TEXT_SIZES


def test_settings_page_offers_at_least_three_sizes(authenticated_client):
    response = authenticated_client.get("/settings")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert len(TEXT_SIZES) >= 3
    for value in TEXT_SIZES:
        assert f'value="{value}"' in html


def test_default_size_when_nothing_saved(authenticated_client):
    response = authenticated_client.get("/settings")
    assert b"text-size-default" in response.data


@pytest.mark.parametrize("size", ["small", "large", "x-large"])
def test_choosing_a_size_saves_it(authenticated_client, size):
    response = authenticated_client.post("/settings", data={"text_size": size})

    assert response.status_code == 302
    assert f"text_size={size}" in response.headers["Set-Cookie"]


@pytest.mark.parametrize("page", ["/", "/login", "/register", "/settings"])
def test_saved_size_is_applied_on_every_page(authenticated_client, page):
    authenticated_client.post("/settings", data={"text_size": "large"})

    if page != "/settings":
        with authenticated_client.session_transaction() as session:
            session.pop("user_id", None)
            session.pop("user_email", None)

    response = authenticated_client.get(page)
    assert b"text-size-large" in response.data


@pytest.mark.parametrize("page", ["/dashboard", "/habit-tracker"])
def test_saved_size_is_applied_on_logged_in_pages(authenticated_client, page):
    authenticated_client.post("/settings", data={"text_size": "large"})

    response = authenticated_client.get(page)
    assert b"text-size-large" in response.data


def test_saved_size_is_selected_on_settings_page(authenticated_client):
    authenticated_client.post("/settings", data={"text_size": "x-large"})

    html = authenticated_client.get("/settings").get_data(as_text=True)
    assert 'value="x-large" checked' in html


def test_reset_restores_default(authenticated_client):
    authenticated_client.post("/settings", data={"text_size": "large"})
    authenticated_client.post("/settings", data={"text_size": "large", "reset": "1"})

    response = authenticated_client.get("/settings")
    assert b"text-size-default" in response.data


def test_invalid_size_falls_back_to_default(authenticated_client):
    authenticated_client.post("/settings", data={"text_size": "huge"})

    response = authenticated_client.get("/settings")
    assert b"text-size-default" in response.data


def test_tampered_cookie_is_ignored(authenticated_client):
    authenticated_client.set_cookie("text_size", '"><script>alert(1)</script>')

    response = authenticated_client.get("/settings")
    assert b"text-size-default" in response.data
    assert b"<script>alert(1)</script>" not in response.data


def test_text_size_class_only_appears_on_html_tag(authenticated_client):
    html = authenticated_client.get("/dashboard").get_data(as_text=True)
    assert html.count("text-size-") == 1
