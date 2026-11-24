from bson import ObjectId
from werkzeug.security import check_password_hash, generate_password_hash


def token(client, path="/settings"):
    client.get(path)
    with client.session_transaction() as session:
        return session["habit_form_token"]


def logged_in(client, mongo_client, username="oldname", password="password"):
    users = mongo_client["Habit-Tracker_test"]["users"]
    user = users.find_one({"email": "person@example.com"})
    user_id = str(user["_id"]) if user and user.get("_id") else str(ObjectId())
    if user:
        users.update_one(
            {"email": "person@example.com"},
            {"$set": {"username": username, "password": generate_password_hash(password)}},
        )
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_email"] = "person@example.com"
        session["username"] = username


def test_settings_requires_login(client):
    response = client.get("/settings")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_update_username_success_preserves_account_data(
    client, mongo_client, habits
):
    logged_in(client, mongo_client)
    habits.insert_one(
        {"name": "Reading", "user_email": "person@example.com", "completed_dates": ["2026-11-20"]}
    )
    csrf = token(client)

    response = client.post(
        "/settings/username",
        data={"csrf_token": csrf, "new_username": "newname"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Username updated successfully" in response.data
    users = mongo_client["Habit-Tracker_test"]["users"]
    assert users.find_one({"email": "person@example.com"})["username"] == "newname"
    assert habits.find_one({"name": "Reading"})["completed_dates"] == ["2026-11-20"]
    with client.session_transaction() as session:
        assert session["username"] == "newname"


def test_update_username_rejects_blank_and_duplicate(client, mongo_client):
    logged_in(client, mongo_client)
    users = mongo_client["Habit-Tracker_test"]["users"]
    users.insert_one({"email": "other@example.com", "username": "taken"})
    csrf = token(client)

    blank = client.post(
        "/settings/username",
        data={"csrf_token": csrf, "new_username": ""},
        follow_redirects=True,
    )
    assert b"Enter a username" in blank.data

    duplicate = client.post(
        "/settings/username",
        data={"csrf_token": csrf, "new_username": "taken"},
        follow_redirects=True,
    )
    assert b"Username is already taken" in duplicate.data
    assert users.find_one({"email": "person@example.com"})["username"] == "oldname"


def test_change_password_success_and_rejects_old_password(client, mongo_client):
    logged_in(client, mongo_client)
    csrf = token(client)

    response = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "password",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Password changed successfully" in response.data
    user = mongo_client["Habit-Tracker_test"]["users"].find_one(
        {"email": "person@example.com"}
    )
    assert check_password_hash(user["password"], "newpassword123")
    assert not check_password_hash(user["password"], "password")


def test_change_password_validation_errors(client, mongo_client):
    logged_in(client, mongo_client)
    csrf = token(client)

    wrong = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "wrong",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        },
        follow_redirects=True,
    )
    assert b"Current password is incorrect" in wrong.data

    mismatch = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "password",
            "new_password": "newpassword123",
            "confirm_password": "different",
        },
        follow_redirects=True,
    )
    assert b"Passwords do not match" in mismatch.data


def test_settings_page_shows_email_as_text_and_one_save_button(client, mongo_client):
    logged_in(client, mongo_client)
    response = client.get("/settings")
    html = response.get_data(as_text=True)

    assert 'id="email_address"' in html
    assert "person@example.com" in html
    assert '<input id="email_address"' not in html
    assert html.count("Save Settings") == 1


def test_save_settings_updates_username_password_and_display_preference(
    client, mongo_client
):
    logged_in(client, mongo_client)
    csrf = token(client)

    response = client.post(
        "/settings",
        data={
            "csrf_token": csrf,
            "action": "save_settings",
            "new_username": "savedname",
            "current_password": "password",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
            "text_size": "large",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "text_size=large" in response.headers["Set-Cookie"]
    users = mongo_client["Habit-Tracker_test"]["users"]
    user = users.find_one({"email": "person@example.com"})
    assert user["username"] == "savedname"
    assert check_password_hash(user["password"], "newpassword123")
    assert "text_size=large" in response.headers["Set-Cookie"]


def test_save_settings_ignores_autofilled_current_password_without_new_password(
    client, mongo_client
):
    logged_in(client, mongo_client)
    csrf = token(client)

    response = client.post(
        "/settings",
        data={
            "csrf_token": csrf,
            "action": "save_settings",
            "new_username": "savedname",
            "current_password": "password",
            "new_password": "",
            "confirm_password": "",
            "text_size": "large",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    message = b"Your settings and preferences saved successfully"
    toast = response.data.split(b'id="success-toast"', 1)[1].split(b"<header", 1)[0]
    assert response.data.count(message) == 1
    assert b'class="fixed ' in toast
    assert message in toast
