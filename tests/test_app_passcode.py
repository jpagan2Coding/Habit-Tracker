"""Tests for the local app passcode (create / change / remove, lock screen, storage)."""

import logging
import sqlite3
from datetime import datetime

import pytest
from bson import ObjectId

from app import PASSCODE_UNLOCKED_SESSION_KEY, passcode_store
from passcode import LocalPasscodeStore, validate_new_passcode

PASSCODE = "4826"
CSRF = "test-csrf-token"


def login(client):
    """Sign in through the real login form, like a new browser session would."""
    response = client.post("/login", data={"email": "person@example.com", "password": "password"})
    assert response.status_code == 302
    with client.session_transaction() as session:
        session["habit_form_token"] = CSRF
        return session["user_id"]


def manage(client, action, **fields):
    return client.post(
        "/settings/passcode",
        data={"csrf_token": CSRF, "action": action, **fields},
        follow_redirects=True,
    )


def create_passcode(client, passcode=PASSCODE):
    return manage(client, "create", new_passcode=passcode, confirm_passcode=passcode)


def new_session(app):
    """A second browser: logs in fresh, so it starts locked when a passcode exists."""
    other = app.test_client()
    login(other)
    return other


def unlock(client, passcode, next_path=None):
    data = {"csrf_token": CSRF, "passcode": passcode}
    if next_path is not None:
        data["next"] = next_path
    return client.post("/unlock", data=data)


@pytest.fixture
def user_id(client):
    return login(client)


@pytest.fixture
def habit_id(habits):
    return str(
        habits.insert_one(
            {
                "name": "Secret Meditation Habit",
                "user_email": "person@example.com",
                "target": 1,
                "frequency": "daily",
                "created_at": datetime(2026, 1, 1),
                "completed_dates": [],
            }
        ).inserted_id
    )


# --- storage -----------------------------------------------------------------


def test_store_hashes_and_verifies_passcode(tmp_path):
    store = LocalPasscodeStore(tmp_path / "passcodes.sqlite3")
    store.set("user-1", "123456")

    assert store.has_passcode("user-1")
    assert store.verify("user-1", "123456")
    assert not store.verify("user-1", "654321")
    assert not store.verify("user-1", "")
    assert not store.has_passcode("user-2")
    assert not store.verify("user-2", "123456")


def test_store_never_saves_plain_text(tmp_path):
    path = tmp_path / "passcodes.sqlite3"
    LocalPasscodeStore(path).set("user-1", "90817263")

    with sqlite3.connect(path) as connection:
        (stored,) = connection.execute("SELECT passcode_hash FROM app_passcodes").fetchone()

    assert stored != "90817263"
    assert stored.startswith(("scrypt:", "pbkdf2:"))
    assert b"90817263" not in path.read_bytes()


def test_store_replace_and_remove(tmp_path):
    store = LocalPasscodeStore(tmp_path / "passcodes.sqlite3")
    store.set("user-1", "1111")
    store.set("user-1", "2222")

    assert not store.verify("user-1", "1111")
    assert store.verify("user-1", "2222")

    store.remove("user-1")
    assert not store.has_passcode("user-1")


def test_store_reads_do_not_create_a_file(tmp_path):
    path = tmp_path / "nested" / "passcodes.sqlite3"
    store = LocalPasscodeStore(path)

    assert not store.has_passcode("user-1")
    store.remove("user-1")
    assert not path.exists()


@pytest.mark.parametrize(
    "passcode, confirm, message",
    [
        ("", "", "Enter a new passcode."),
        ("123", "123", "4 to 12 digits"),
        ("1234567890123", "1234567890123", "4 to 12 digits"),
        ("12ab", "12ab", "4 to 12 digits"),
        ("1234", "", "Confirm your new passcode."),
        ("1234", "4321", "Passcodes do not match."),
    ],
)
def test_validate_new_passcode_rejects_bad_input(passcode, confirm, message):
    assert message in validate_new_passcode(passcode, confirm)


def test_validate_new_passcode_accepts_digits():
    assert validate_new_passcode("1234", "1234") is None
    assert validate_new_passcode("123456789012", "123456789012") is None


# --- create ------------------------------------------------------------------


def test_create_passcode_from_settings(client, user_id, mongo_client):
    response = create_passcode(client)

    assert response.status_code == 200
    assert b"Passcode created" in response.data
    assert b"Passcode is on." in response.data
    assert passcode_store().verify(user_id, PASSCODE)

    # Nothing about the passcode is saved to the cloud database.
    user = mongo_client["Habit-Tracker_test"]["users"].find_one({"email": "person@example.com"})
    assert not any("passcode" in key for key in user)
    assert PASSCODE not in str(user)


def test_settings_passcode_inputs_are_masked(client, user_id):
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="new_passcode" name="new_passcode" type="password"' in page
    assert 'id="confirm_passcode" name="confirm_passcode" type="password"' in page

    create_passcode(client)
    page = client.get("/settings").get_data(as_text=True)
    assert PASSCODE not in page
    assert 'name="current_passcode" type="password"' in page


@pytest.mark.parametrize(
    "new, confirm, message",
    [
        ("12", "12", b"4 to 12 digits"),
        ("abcd", "abcd", b"4 to 12 digits"),
        ("1234", "9999", b"Passcodes do not match"),
    ],
)
def test_create_passcode_rejects_invalid_values(client, user_id, new, confirm, message):
    response = manage(client, "create", new_passcode=new, confirm_passcode=confirm)

    assert message in response.data
    assert not passcode_store().has_passcode(user_id)


def test_create_passcode_when_one_exists_is_rejected(client, user_id):
    create_passcode(client)
    response = create_passcode(client, "9999")

    assert b"A passcode is already set" in response.data
    assert passcode_store().verify(user_id, PASSCODE)


def test_creating_passcode_keeps_current_session_unlocked(client, user_id, habit_id):
    create_passcode(client)

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"Secret Meditation Habit" in response.data


def test_passcode_actions_require_csrf_and_login(client, user_id):
    response = client.post(
        "/settings/passcode",
        data={"action": "create", "new_passcode": PASSCODE, "confirm_passcode": PASSCODE},
    )
    assert response.status_code == 400
    assert not passcode_store().has_passcode(user_id)

    anonymous = client.application.test_client()
    response = anonymous.post("/settings/passcode", data={"action": "create"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


# --- lock screen -------------------------------------------------------------


def test_new_session_must_enter_passcode_before_seeing_habits(app, client, user_id, habit_id):
    create_passcode(client)
    other = new_session(app)

    response = other.get("/dashboard")
    assert response.status_code == 302
    assert "/unlock?next=/dashboard" in response.headers["Location"]

    page = other.get("/unlock?next=/dashboard")
    assert page.status_code == 200
    assert b"Unlock Habit Tracker" in page.data
    assert b"Secret Meditation Habit" not in page.data


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/dashboard",
        "/habit-tracker",
        "/summary",
        "/settings",
        "/account/delete",
        "/account/delete-data",
        "/habits/{habit_id}/history",
        "/habits/{habit_id}/export",
        "/habits/{habit_id}/edit",
        "/habits/{habit_id}/record-completion",
    ],
)
def test_locked_session_cannot_open_any_habit_page(app, client, user_id, habit_id, path):
    create_passcode(client)
    other = new_session(app)

    response = other.get(path.format(habit_id=habit_id))

    assert response.status_code == 302
    assert "/unlock" in response.headers["Location"]
    assert b"Secret Meditation Habit" not in response.data


def test_locked_session_cannot_change_habits(app, client, user_id, habit_id, habits):
    create_passcode(client)
    other = new_session(app)

    complete = other.post(f"/habits/{habit_id}/complete", data={"csrf_token": CSRF})
    assert complete.status_code == 401
    assert complete.get_json()["error"] == "The app is locked. Enter your passcode to continue."

    delete = other.post(
        "/habits/delete-selected", data={"csrf_token": CSRF, "habit_ids": [habit_id]}
    )
    assert delete.status_code == 302
    assert "/unlock" in delete.headers["Location"]

    habit = habits.find_one({"_id": ObjectId(habit_id)})
    assert habit is not None
    assert habit["completed_dates"] == []


def test_locked_session_api_returns_json(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    response = other.get("/api/push-status")
    assert response.status_code == 401
    assert response.get_json()["unlock_url"] == "/unlock"


def test_incorrect_passcode_shows_error_and_stays_locked(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    response = unlock(other, "0000")

    assert response.status_code == 401
    assert b"Incorrect passcode. Please try again." in response.data
    assert other.get("/dashboard").status_code == 302


def test_blank_passcode_shows_error(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    response = unlock(other, "")
    assert response.status_code == 400
    assert b"Enter your passcode." in response.data


def test_repeated_incorrect_attempts_are_not_limited(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    for _ in range(10):
        assert unlock(other, "0000").status_code == 401

    assert unlock(other, PASSCODE).status_code == 302


def test_correct_passcode_continues_into_app(app, client, user_id, habit_id):
    create_passcode(client)
    other = new_session(app)

    response = unlock(other, PASSCODE, next_path="/summary")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/summary")

    dashboard = other.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"Secret Meditation Habit" in dashboard.data


@pytest.mark.parametrize("next_path", ["//evil.example.com", "https://evil.example.com", "/\\evil"])
def test_unlock_ignores_external_next_urls(app, client, user_id, next_path):
    create_passcode(client)
    other = new_session(app)

    response = unlock(other, PASSCODE, next_path=next_path)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_unlock_requires_csrf_token(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    response = other.post("/unlock", data={"passcode": PASSCODE})
    assert response.status_code == 400


def test_login_again_relocks_the_app(client, user_id):
    create_passcode(client)
    assert client.get("/dashboard").status_code == 200

    client.post("/logout")
    login(client)

    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/unlock" in response.headers["Location"]


def test_locked_user_can_log_out(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    response = other.post("/logout")
    assert response.status_code == 302
    with other.session_transaction() as session:
        assert "user_id" not in session


def test_lock_button_locks_current_session(client, user_id):
    create_passcode(client)
    assert b"Lock App" in client.get("/dashboard").data

    response = client.post("/lock")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/unlock")
    assert client.get("/dashboard").status_code == 302


def test_lock_without_passcode_explains_what_to_do(client, user_id):
    assert b"Lock App" not in client.get("/dashboard").data

    response = client.post("/lock", follow_redirects=True)
    assert b"Create a passcode in Settings" in response.data


def test_users_without_passcode_are_not_affected(client, user_id, habit_id):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert client.get("/unlock").headers["Location"].endswith("/dashboard")


def test_passcode_belongs_to_one_account(app, client, user_id, mongo_client):
    create_passcode(client)

    other_id = str(ObjectId())
    other = app.test_client()
    with other.session_transaction() as session:
        session["user_id"] = other_id
        session["user_email"] = "someone-else@example.com"

    assert other.get("/dashboard").status_code == 200
    assert not passcode_store().has_passcode(other_id)


# --- change ------------------------------------------------------------------


def test_change_passcode(app, client, user_id):
    create_passcode(client)

    response = manage(
        client,
        "change",
        current_passcode=PASSCODE,
        new_passcode="73519",
        confirm_passcode="73519",
    )
    assert b"Passcode changed successfully." in response.data

    other = new_session(app)
    assert unlock(other, PASSCODE).status_code == 401
    assert unlock(other, "73519").status_code == 302


def test_change_passcode_requires_correct_current_passcode(client, user_id):
    create_passcode(client)

    response = manage(
        client,
        "change",
        current_passcode="0000",
        new_passcode="73519",
        confirm_passcode="73519",
    )
    assert b"Current passcode is incorrect." in response.data
    assert passcode_store().verify(user_id, PASSCODE)

    blank = manage(client, "change", new_passcode="73519", confirm_passcode="73519")
    assert b"Enter your current passcode." in blank.data


def test_change_passcode_validates_new_value(client, user_id):
    create_passcode(client)

    response = manage(
        client, "change", current_passcode=PASSCODE, new_passcode="12", confirm_passcode="12"
    )
    assert b"4 to 12 digits" in response.data
    assert passcode_store().verify(user_id, PASSCODE)


# --- remove ------------------------------------------------------------------


def test_remove_passcode(app, client, user_id):
    create_passcode(client)

    response = manage(client, "remove", current_passcode=PASSCODE)
    assert b"Passcode removed." in response.data
    assert b"No passcode is set." in response.data
    assert not passcode_store().has_passcode(user_id)

    other = new_session(app)
    assert other.get("/dashboard").status_code == 200


def test_remove_passcode_requires_correct_current_passcode(client, user_id):
    create_passcode(client)

    response = manage(client, "remove", current_passcode="0000")
    assert b"Current passcode is incorrect." in response.data
    assert passcode_store().has_passcode(user_id)


def test_change_or_remove_without_passcode(client, user_id):
    response = manage(client, "remove", current_passcode=PASSCODE)
    assert b"You don&#39;t have a passcode yet." in response.data


def test_unknown_passcode_action_is_rejected(client, user_id):
    response = client.post("/settings/passcode", data={"csrf_token": CSRF, "action": "nope"})
    assert response.status_code == 400


# --- forgotten passcode ------------------------------------------------------


def test_forgotten_passcode_can_be_removed_with_account_password(app, client, user_id):
    create_passcode(client)
    other = new_session(app)

    wrong = other.post(
        "/unlock",
        data={"csrf_token": CSRF, "action": "reset", "account_password": "not-it"},
    )
    assert wrong.status_code == 401
    assert b"Incorrect account password." in wrong.data
    assert passcode_store().has_passcode(user_id)

    response = other.post(
        "/unlock",
        data={"csrf_token": CSRF, "action": "reset", "account_password": "password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Your passcode was removed" in response.data
    assert not passcode_store().has_passcode(user_id)


# --- account deletion & logging ----------------------------------------------


def test_deleting_account_removes_local_passcode(client, user_id):
    create_passcode(client)

    client.post("/account/delete", data={"csrf_token": CSRF})

    assert not passcode_store().has_passcode(user_id)


def test_passcode_is_never_logged(app, client, user_id, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "86420975"

    create_passcode(client, secret)
    unlock(client, "11112222")
    manage(client, "change", current_passcode=secret, new_passcode="1357", confirm_passcode="1357")
    client.post("/lock")
    unlock(client, "1357")
    manage(client, "remove", current_passcode="1357")

    assert secret not in caplog.text
    assert "1357" not in caplog.text


def test_unlocked_flag_is_cleared_on_remove(client, user_id):
    create_passcode(client)
    with client.session_transaction() as session:
        assert session[PASSCODE_UNLOCKED_SESSION_KEY] is True

    manage(client, "remove", current_passcode=PASSCODE)
    with client.session_transaction() as session:
        assert PASSCODE_UNLOCKED_SESSION_KEY not in session
