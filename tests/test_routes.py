from datetime import datetime

import pytest
from werkzeug.security import check_password_hash

from models import Habit

# === Habit Tracker Tests ===


def test_habit_tracker_get_returns_ok(client):
    """Test that GET /habit-tracker returns a 200 status code."""
    response = client.get("/habit-tracker")

    assert response.status_code == 200


def test_habit_tracker_post_creates_habit(client):
    """Test that POST /habit-tracker creates a new habit in the database."""
    habit_data = {
        "name": "Read 20 pages",
        "description": "Daily reading goal",
        "target": "1",
        "frequency": "daily",
    }

    response = client.post(
        "/habit-tracker",
        data=habit_data,
        follow_redirects=False,
    )

    assert response.status_code == 302

    stored = Habit.query.filter_by(name="Read 20 pages").first()

    assert stored is not None
    assert stored.description == "Daily reading goal"


# === Dashboard Tests ===


def test_dashboard_get_returns_ok(client):
    """Test that GET /dashboard returns a 200 status code."""
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"Dashboard" in response.data
    assert b"Today's Habits" in response.data
    assert b"Total Habits" in response.data
    assert b"All My Habits" in response.data


def test_dashboard_displays_date_and_welcome(client):
    """Dashboard displays the supplied date and welcome message."""
    from flask import template_rendered

    captured_contexts = []

    def record_template(sender, template, context, **extra):
        if template.name == "apps/habit_tracker/dashboard.html":
            captured_contexts.append(context)

    with template_rendered.connected_to(
        record_template,
        client.application,
    ):
        response = client.get("/dashboard")

    assert response.status_code == 200
    assert len(captured_contexts) == 1

    today = captured_contexts[0]["today"]

    assert today
    assert f"Today: {today}".encode() in response.data
    assert b"Welcome back!" in response.data

# === Login Tests ===

def test_login_get_returns_ok(client):
    """Test that the login page renders successfully."""
    response = client.get('/login')

    assert response.status_code == 200
    assert b'Log in to HabitTracker' in response.data
    assert b'Email address' in response.data


def test_login_requires_both_fields(client):
    """Test that an incomplete login shows a helpful validation message."""
    response = client.post('/login', data={'email': '', 'password': ''})

    assert response.status_code == 200
    assert b'Enter both your email and password' in response.data


def test_login_redirects_to_dashboard_with_credentials(client):
    """Test the temporary demo login flow."""
    response = client.post(
        '/login',
        data={'email': 'person@example.com', 'password': 'password'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


# === Parametrized Tests ===


@pytest.mark.parametrize(
    "endpoint",
    ["/habit-tracker", "/dashboard"],
)
def test_all_pages_get_returns_ok(client, endpoint):
    """Test that Habit Tracker pages return 200 status code on GET requests."""
    response = client.get(endpoint)

    assert response.status_code == 200


# === Registration Tests ===


def test_register_get_returns_ok(client):
    response = client.get("/register")
    assert response.status_code == 200
    assert b"Create an Account" in response.data


def test_register_post_creates_user(client, mongo_client):
    data = {
        "username": "newuser",
        "email": "NewUser@Example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    response = client.post("/register", data=data, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/dashboard"
    assert b"Registration successful" in response.data

    database = mongo_client["habit_tracker_test"]
    user = database["users"].find_one({"username": "newuser"})
    assert user is not None
    # Stored the way the login route reads it: lowercase email, hash under "password".
    assert user["email"] == "newuser@example.com"
    assert check_password_hash(user["password"], "password123")
    assert "password_hash" not in user
    assert isinstance(user["created_at"], datetime)

    with client.session_transaction() as sess:
        assert sess["user_id"] == str(user["_id"])
        assert sess["username"] == "newuser"
        assert sess["user_email"] == "newuser@example.com"


def test_registered_user_can_log_in(client, mongo_client):
    data = {
        "username": "loginlater",
        "email": "loginlater@example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    assert client.post("/register", data=data).status_code == 302
    client.post("/logout")

    response = client.post(
        "/login",
        data={"email": "loginlater@example.com", "password": "password123"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_register_creates_unique_indexes(client, mongo_client):
    data = {
        "username": "indexed",
        "email": "indexed@example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    client.post("/register", data=data)

    indexes = mongo_client["habit_tracker_test"]["users"].index_information()
    assert indexes["email_unique"]["unique"] is True
    assert indexes["username_unique"]["unique"] is True
    assert indexes["username_unique"]["sparse"] is True


def test_register_post_rejects_mismatched_passwords(client, mongo_client):
    data = {
        "username": "mismatch",
        "email": "mismatch@example.com",
        "password": "password123",
        "confirm_password": "differentpassword",
    }
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert b"Passwords do not match" in response.data

    database = mongo_client["habit_tracker_test"]
    assert database["users"].find_one({"username": "mismatch"}) is None


def test_register_post_rejects_short_password(client, mongo_client):
    data = {
        "username": "shorty",
        "email": "shorty@example.com",
        "password": "short",
        "confirm_password": "short",
    }
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert b"at least 8 characters" in response.data
    assert mongo_client["habit_tracker_test"]["users"].find_one({"username": "shorty"}) is None


def test_register_post_rejects_duplicate_username(client, mongo_client):
    database = mongo_client["habit_tracker_test"]
    database["users"].insert_one({
        "username": "existinguser",
        "email": "existing@example.com",
        "password": "hashedpassword",
    })
    data = {
        "username": "existinguser",
        "email": "unique@example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert b"Username is already taken" in response.data


def test_register_post_rejects_duplicate_email(client, mongo_client):
    database = mongo_client["habit_tracker_test"]
    database["users"].insert_one({
        "username": "uniqueuser",
        "email": "duplicate@example.com",
        "password": "hashedpassword",
    })
    data = {
        "username": "anotheruser",
        "email": "Duplicate@Example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert b"Email is already registered" in response.data
    assert database["users"].count_documents({"email": "duplicate@example.com"}) == 1


def test_register_handles_duplicate_created_between_check_and_insert(
    client, mongo_client, monkeypatch
):
    """A concurrent registration that wins the race is caught by the unique index."""
    import app as app_module

    database = mongo_client["habit_tracker_test"]
    app_module.ensure_user_indexes(database["users"])
    real_hash = app_module.generate_password_hash

    def hash_after_competitor_registers(password):
        database["users"].insert_one({
            "username": "competitor",
            "email": "race@example.com",
            "password": "hashedpassword",
        })
        return real_hash(password)

    monkeypatch.setattr(app_module, "generate_password_hash", hash_after_competitor_registers)

    data = {
        "username": "racer",
        "email": "race@example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert b"Email is already registered" in response.data
    assert database["users"].count_documents({"email": "race@example.com"}) == 1
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_register_without_database_shows_error(client, monkeypatch):
    import app as app_module

    monkeypatch.setitem(app_module.app.config, "MONGO_DB", None)
    monkeypatch.setattr(app_module, "_load_env_files", lambda: None)
    monkeypatch.delenv("MONGODB_URI", raising=False)

    data = {
        "username": "nodb",
        "email": "nodb@example.com",
        "password": "password123",
        "confirm_password": "password123",
    }
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert b"Database connection error" in response.data


def test_register_redirects_if_already_logged_in(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    response = client.get("/register")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_navbar_shows_register_when_logged_out_and_hides_when_logged_in(client):
    # Logged out: Register link is present
    res_logged_out = client.get("/habit-tracker")
    assert b"Register" in res_logged_out.data

    # Logged in: Register link is hidden
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    res_logged_in = client.get("/dashboard")
    assert b"Register" not in res_logged_in.data
