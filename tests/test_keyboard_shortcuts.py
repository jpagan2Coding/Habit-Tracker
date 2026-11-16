from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect


@pytest.fixture
def shortcut_page(page, authenticated_client):
    """Serve Flask pages to WebKit using the mocked test database."""

    def handle_request(route):
        parsed = urlsplit(route.request.url)

        if parsed.hostname != "habit-tracker.test":
            route.abort()
            return

        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        response = authenticated_client.get(path)

        route.fulfill(
            status=response.status_code,
            headers={"Content-Type": response.content_type},
            body=response.get_data(),
        )

    page.route("**/*", handle_request)
    page.goto("http://habit-tracker.test/dashboard")

    expect(page.locator("#keyboard-shortcuts-enabled")).to_be_checked()

    return page


def press_shortcut(page, key):
    """Run a shortcut after moving focus outside form controls."""
    page.locator("h1").click()
    page.keyboard.press("g")
    page.keyboard.press(key)


def disable_shortcuts(page):
    """Disable shortcuts through the checkbox's keyboard interaction."""
    toggle = page.locator("#keyboard-shortcuts-enabled")

    expect(toggle).to_be_checked()
    toggle.focus()
    toggle.press("Space")

    expect(toggle).not_to_be_checked()
    expect(page.locator("#keyboard-shortcut-status")).to_have_text(
        "Keyboard shortcuts disabled."
    )


@pytest.mark.parametrize(
    ("key", "path", "message"),
    [
        (
            "n",
            "/habit-tracker",
            "Keyboard shortcut: opened Add New Habit.",
        ),
        (
            "s",
            "/settings",
            "Keyboard shortcut: opened Settings.",
        ),
    ],
)
def test_navigation_shortcuts(shortcut_page, key, path, message):
    page = shortcut_page

    press_shortcut(page, key)

    expect(page).to_have_url(f"http://habit-tracker.test{path}")
    expect(page.locator("#keyboard-navigation-feedback")).to_be_visible()
    expect(page.locator("#keyboard-navigation-feedback")).to_have_text(message)


def test_notification_shortcut_opens_and_closes(shortcut_page):
    page = shortcut_page
    bell = page.locator("#notification-bell")
    status = page.locator("#keyboard-shortcut-status")

    page.locator("h1").click()

    page.keyboard.press("g")
    page.keyboard.press("b")

    expect(bell).to_have_attribute("aria-expanded", "true")
    expect(status).to_have_text("Notifications opened.")

    # Do not click outside the dropdown before the second shortcut.
    page.keyboard.press("g")
    page.keyboard.press("b")

    expect(bell).to_have_attribute("aria-expanded", "false")
    expect(status).to_have_text("Notifications closed.")


@pytest.mark.parametrize("key", ["n", "s", "b"])
def test_disabled_shortcuts_do_nothing(shortcut_page, key):
    page = shortcut_page

    disable_shortcuts(page)
    press_shortcut(page, key)

    expect(page).to_have_url("http://habit-tracker.test/dashboard")
    expect(page.locator("#notification-bell")).to_have_attribute(
        "aria-expanded", "false"
    )
    expect(page.locator("#keyboard-shortcut-status")).to_have_text(
        "Keyboard shortcuts disabled."
    )


def test_disabled_preference_survives_reload(shortcut_page):
    page = shortcut_page

    disable_shortcuts(page)
    page.reload()

    expect(page.locator("#keyboard-shortcuts-enabled")).not_to_be_checked()
    expect(page.locator("#keyboard-shortcut-status")).to_have_text(
        "Keyboard shortcuts disabled."
    )


@pytest.mark.parametrize(
    "markup",
    [
        '<input id="typing-test" type="text">',
        '<textarea id="typing-test"></textarea>',
        '<select id="typing-test"><option>Example</option></select>',
        '<div id="typing-test" contenteditable="true"></div>',
        '<div id="typing-test" role="textbox" tabindex="0"></div>',
    ],
)
def test_shortcuts_do_not_run_in_entry_fields(shortcut_page, markup):
    page = shortcut_page

    page.evaluate(
        "(markup) => document.body.insertAdjacentHTML('beforeend', markup)",
        markup,
    )

    field = page.locator("#typing-test")
    field.focus()

    for key in ("n", "s", "b"):
        page.keyboard.press("g")
        page.keyboard.press(key)

    expect(page).to_have_url("http://habit-tracker.test/dashboard")
    expect(page.locator("#notification-bell")).to_have_attribute(
        "aria-expanded", "false"
    )


def test_expired_sequence_does_nothing(shortcut_page):
    page = shortcut_page
    page.locator("h1").click()

    page.clock.install()
    page.keyboard.press("g")
    page.clock.fast_forward(1100)
    page.keyboard.press("s")

    expect(page).to_have_url("http://habit-tracker.test/dashboard")


@pytest.mark.parametrize("modifier", ["Control", "Meta", "Alt", "Shift"])
def test_modified_keys_do_not_trigger_shortcuts(shortcut_page, modifier):
    page = shortcut_page
    page.locator("h1").click()

    page.keyboard.press(f"{modifier}+g")
    page.keyboard.press("s")

    expect(page).to_have_url("http://habit-tracker.test/dashboard")


@pytest.mark.parametrize(
    "extra",
    [
        {"repeat": True},
        {"isComposing": True},
    ],
)
def test_repeated_or_composing_keys_are_ignored(shortcut_page, extra):
    page = shortcut_page
    page.locator("h1").click()
    page.keyboard.press("g")

    page.evaluate(
        """extra => {
            document.body.dispatchEvent(new KeyboardEvent("keydown", {
                key: "s",
                bubbles: true,
                cancelable: true,
                ...extra
            }));
        }""",
        extra,
    )

    expect(page).to_have_url("http://habit-tracker.test/dashboard")


@pytest.mark.parametrize(
    ("key", "target_id", "message"),
    [
        (
            "n",
            "shortcut-new-habit",
            "This shortcut is currently unavailable.",
        ),
        (
            "s",
            "shortcut-settings",
            "This shortcut is currently unavailable.",
        ),
        (
            "b",
            "notification-bell",
            "Notifications are currently unavailable.",
        ),
    ],
)
def test_missing_target_shows_feedback(
    shortcut_page, key, target_id, message
):
    page = shortcut_page

    page.locator(f"#{target_id}").evaluate(
        "(element) => element.remove()"
    )

    press_shortcut(page, key)

    expect(page).to_have_url("http://habit-tracker.test/dashboard")
    expect(page.locator("#keyboard-shortcut-status")).to_have_text(message)