import os
from datetime import datetime
from pathlib import Path

import certifi
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash

from models import FREQUENCY_CHOICES

load_dotenv(Path(__file__).resolve().with_name("atlas-credentials.env"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")


def _mongo_client_options(mongo_uri):
    """Connection options for MongoClient.

    Atlas requires TLS, and the python.org build of Python ships without a CA
    bundle, so certificate verification fails with CERTIFICATE_VERIFY_FAILED
    unless pymongo is pointed at certifi's bundle. Plain mongodb:// URIs (a local
    mongod) are left untouched because tlsCAFile would force TLS on.
    """
    options = {"serverSelectionTimeoutMS": 5000}
    lowered = mongo_uri.lower()
    if lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered:
        options["tlsCAFile"] = certifi.where()
    return options


def get_db():
    """Connect to the team's MongoDB database using MONGODB_URI."""
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        raise RuntimeError("MONGODB_URI is not configured")

    client = MongoClient(mongo_uri, **_mongo_client_options(mongo_uri))
    return client[os.getenv("MONGODB_DATABASE", "Habit-Tracker")]


def ensure_user_indexes(users):
    """Unique indexes so two registrations can never create the same email/username.

    create_index is idempotent. The username index is sparse because accounts
    seeded for the login story may not have a username field.
    """
    users.create_index([("email", ASCENDING)], unique=True, name="email_unique")
    users.create_index(
        [("username", ASCENDING)], unique=True, sparse=True, name="username_unique"
    )


@app.route("/")
def home():
    return render_template("home/index.html", page_id="home")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Render the login screen and authenticate an existing user."""
    error = None
    email = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Enter both your email and password to continue."
        else:
            try:
                user = get_db()["users"].find_one({"email": email})
            except (OSError, PyMongoError, RuntimeError):
                user = None

            stored_password = user.get("password", "") if user else ""
            if user and check_password_hash(stored_password, password):
                session["user_id"] = str(user.get("_id", ""))
                session["username"] = user.get("username", email)
                session["user_email"] = email
                return redirect(url_for("habit_dashboard"))

            error = "Invalid email or password."

    return render_template(
        "auth/login.html",
        page_id="login",
        error=error,
        email=email,
    )


@app.route("/habit-tracker", methods=["GET", "POST"])
def habit_tracker():
    form = {"name": "", "description": "", "target": "", "frequency": ""}
    errors = []

    if request.method == "POST":
        form = {key: request.form.get(key, "").strip() for key in form}

        if not form["name"]:
            errors.append("Enter a name for your habit.")

        target = None
        if not form["target"]:
            errors.append("Enter a target.")
        elif not form["target"].isdigit() or int(form["target"]) < 1:
            errors.append("Target must be a whole number of 1 or more.")
        else:
            target = int(form["target"])

        if form["frequency"] not in FREQUENCY_CHOICES:
            errors.append("Choose a frequency.")

        if not errors:
            get_db()["habits"].insert_one(
                {
                    "name": form["name"],
                    "description": form["description"] or None,
                    "target": target,
                    "frequency": form["frequency"],
                    "user_email": session.get("user_email"),
                    "created_at": datetime.utcnow(),
                    "completed_dates": [],
                }
            )
            return redirect(url_for("habit_dashboard"))

    return render_template(
        "apps/habit_tracker/index.html",
        page_id="habit-tracker",
        form=form,
        errors=errors,
        frequency_choices=FREQUENCY_CHOICES,
    ), (400 if errors else 200)


@app.route("/dashboard")
def habit_dashboard():
    habits = list(
        get_db()["habits"]
        .find({"user_email": session.get("user_email")})
        .sort("created_at", -1)
    )
    for habit in habits:
        habit["id"] = str(habit["_id"])

    today = datetime.utcnow().date().isoformat()

    completion_by_id = {
        habit["id"]: today in habit.get("completed_dates", [])
        for habit in habits
    }

    completed_today = sum(completion_by_id.values())

    total_habits = len(habits)
    scheduled_today = total_habits

    completion_percentage = (
        round((completed_today / scheduled_today) * 100) if scheduled_today else 0
    )

    return render_template(
        "apps/habit_tracker/dashboard.html",
        page_id="dashboard",
        habits=habits,
        completion_by_id=completion_by_id,
        total_habits=total_habits,
        completed_today=completed_today,
        scheduled_today=scheduled_today,
        completion_percentage=completion_percentage,
        current_streak=None,
        today=today,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    """Create a new account in the MongoDB users collection and log it in."""
    if session.get("user_id"):
        return redirect(url_for("habit_dashboard"))

    username = ""
    email = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        error = None
        if not username or not email or not password:
            error = "All fields are required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters long."
        elif password != confirm_password:
            error = "Passwords do not match."

        if error is None:
            try:
                users = get_db()["users"]
            except (OSError, PyMongoError, RuntimeError) as e:
                app.logger.error(f"Database connection error during registration: {e}")
                error = "Database connection error. Please try again later."
            else:
                error = _create_user(users, username, email, password)

        if error is None:
            flash("Registration successful! Welcome to Habit Tracker.", "success")
            return redirect(url_for("habit_dashboard"))

        flash(error, "error")

    return render_template(
        "auth/register.html", page_id="register", username=username, email=email
    )


def _create_user(users, username, email, password):
    """Insert a user document shaped the way login reads it. Returns an error or None."""
    try:
        ensure_user_indexes(users)
    except (OSError, PyMongoError) as e:
        # Pre-existing duplicate data would block index creation; the checks below still run.
        app.logger.warning(f"Could not ensure user indexes: {e}")

    try:
        if users.find_one({"username": username}):
            return "Username is already taken. Please choose another."
        if users.find_one({"email": email}):
            return "Email is already registered. Please log in."

        result = users.insert_one(
            {
                "username": username,
                "email": email,
                "password": generate_password_hash(password),
                "created_at": datetime.utcnow(),
            }
        )
    except DuplicateKeyError:
        # Another request registered the same email/username between our check and insert.
        return _duplicate_user_error(users, username, email)
    except (OSError, PyMongoError) as e:
        app.logger.error(f"Error creating user: {e}")
        return "Database error occurred while creating account."

    session["user_id"] = str(result.inserted_id)
    session["username"] = username
    session["user_email"] = email
    return None


def _duplicate_user_error(users, username, email):
    """Explain which unique field collided after a DuplicateKeyError."""
    try:
        if users.find_one({"email": email}):
            return "Email is already registered. Please log in."
        if users.find_one({"username": username}):
            return "Username is already taken. Please choose another."
    except (OSError, PyMongoError):
        pass
    return "That email or username is already in use."


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))  # change to login once implemented


if __name__ == "__main__":
    app.run(debug=True)
