import csv
import io
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import certifi
from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from models import FREQUENCY_CHOICES
from progress import (
    VALID_PROGRESS_PERIODS,
    calculate_overall_progress,
    is_scheduled_on,
)


def _is_valid_timezone(name):
    if name == "UTC":
        return True
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True

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
    form = {
        "name": "",
        "description": "",
        "target": "",
        "frequency": "",
        "reminder_enabled": False,
        "reminder_time": "",
        "reminder_timezone": "UTC",
    }
    errors = []

    if request.method == "POST":
        form.update(
            {
                "name": request.form.get("name", "").strip(),
                "description": request.form.get("description", "").strip(),
                "target": request.form.get("target", "").strip(),
                "frequency": request.form.get("frequency", "").strip(),
                "reminder_enabled": request.form.get("reminder_enabled") == "on",
                "reminder_time": request.form.get("reminder_time", "").strip(),
                "reminder_timezone": request.form.get("reminder_timezone", "UTC").strip(),
            }
        )

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
        if form["reminder_enabled"]:
            try:
                datetime.strptime(form["reminder_time"], "%H:%M")
            except ValueError:
                errors.append("Enter a valid reminder time.")
            if not _is_valid_timezone(form["reminder_timezone"]):
                errors.append("Choose a valid time zone.")

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
                    "reminder_enabled": form["reminder_enabled"],
                    "reminder_time": form["reminder_time"] or None,
                    "reminder_timezone": form["reminder_timezone"] or "UTC",
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

# Habits with these statuses don't show up on the "today" list
INACTIVE_STATUSES = ("paused", "archived", "expired")


def _period_start(frequency, today):
    # start of the current day / week (Monday) / month
    if frequency == "weekly":
        return today - timedelta(days=today.weekday())
    if frequency == "monthly":
        return today.replace(day=1)
    return today


def is_due_today(habit, today):
    """Check if a habit should show up on today's list."""
    if habit.get("status", "active") in INACTIVE_STATUSES:
        return False

    today_iso = today.isoformat()
    completed = {d for d in habit.get("completed_dates", []) if isinstance(d, str)}

    # keep habits finished today on the list so they show as done
    if today_iso in completed:
        return True

    # reminder: add skipped habits here once Skip a Habit / Undo Habit Completion are done

    # due until the target is hit for this period
    start_iso = _period_start(habit.get("frequency") or "daily", today).isoformat()
    completed_this_period = sum(1 for d in completed if start_iso <= d <= today_iso)
    return completed_this_period < (habit.get("target") or 1)

@app.route("/dashboard")
def habit_dashboard():
    habits = list(
        get_db()["habits"]
        .find({"user_email": session.get("user_email")})
        .sort("created_at", -1)
    )
    for habit in habits:
        habit["id"] = str(habit["_id"])

    today_date = datetime.utcnow().date()
    today = today_date.isoformat()
    try:
        progress_period = int(request.args.get("period", 7))
    except ValueError:
        progress_period = 7
    if progress_period not in VALID_PROGRESS_PERIODS:
        progress_period = 7

    overall_progress = calculate_overall_progress(
        habits, end_date=today_date, period_days=progress_period
    )

    todays_habits = [habit for habit in habits if is_due_today(habit, today_date)]

    completion_by_id = {
        habit["id"]: today in habit.get("completed_dates", [])
        for habit in habits
    }

    completed_today = sum(completion_by_id[habit["id"]] for habit in todays_habits)

    total_habits = len(habits)
    scheduled_today = sum(is_scheduled_on(habit, today_date) for habit in habits)

    completion_percentage = (
        round((completed_today / scheduled_today) * 100) if scheduled_today else 0
    )
    notifications = []
    unread_notification_count = 0
    if session.get("user_email"):
        notifications_collection = get_db()["notifications"]
        notifications = list(
            notifications_collection.find({"user_email": session["user_email"]})
            .sort("created_at", -1)
            .limit(10)
        )
        unread_notification_count = notifications_collection.count_documents(
            {"user_email": session["user_email"], "read_at": {"$exists": False}}
        )

    return render_template(
        "apps/habit_tracker/dashboard.html",
        page_id="dashboard",
        habits=habits,
        todays_habits=todays_habits,
        completion_by_id=completion_by_id,
        total_habits=total_habits,
        completed_today=completed_today,
        scheduled_today=scheduled_today,
        completion_percentage=completion_percentage,
        overall_progress=overall_progress,
        progress_periods=VALID_PROGRESS_PERIODS,
        current_streak=None,
        today=today,
        habit_csrf_token=_habit_form_token(),
        notifications=notifications,
        unread_notification_count=unread_notification_count,
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


@app.route("/api/push-subscriptions", methods=["POST"])
def save_push_subscription():
    if not session.get("user_id") or not session.get("user_email"):
        return {"error": "Login required."}, 401
    payload = request.get_json(silent=True) or {}
    subscription = payload.get("subscription")
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        return {"error": "Invalid push subscription."}, 400
    get_db()["push_subscriptions"].update_one(
        {"user_email": session["user_email"]},
        {"$set": {"subscription": subscription, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    return {"ok": True}


@app.route("/api/push-public-key")
def push_public_key():
    if not session.get("user_id") or not session.get("user_email"):
        return {"error": "Login required."}, 401
    public_key = os.getenv("VAPID_PUBLIC_KEY")
    if not public_key:
        return {"error": "Push notifications are not configured."}, 503
    return {"public_key": public_key}

def _habit_form_token():
    token = session.get("habit_form_token")
    if not token:
        token = secrets.token_hex(32)
        session["habit_form_token"] = token
    return token


def _validate_habit_form_token():
    expected = session.get("habit_form_token")
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not secrets.compare_digest(expected, submitted):
        abort(400, description="Invalid form token. Reload the form and try again.")


def _owned_habit(habit_id):
    if not ObjectId.is_valid(habit_id):
        abort(404)

    query = {
        "_id": ObjectId(habit_id),
        "user_email": session["user_email"],
    }
    habit = get_db()["habits"].find_one(query)
    if habit is None:
        abort(404)
    return query, habit


def _owned_notification(notification_id):
    if not ObjectId.is_valid(notification_id):
        abort(404)
    query = {
        "_id": ObjectId(notification_id),
        "user_email": session["user_email"],
    }
    notification = get_db()["notifications"].find_one(query)
    if notification is None:
        abort(404)
    return query, notification


@app.route("/api/notifications/<notification_id>/read", methods=["POST"])
def mark_notification_read(notification_id):
    if not session.get("user_id") or not session.get("user_email"):
        return {"error": "Login required."}, 401
    _validate_habit_form_token()
    query, notification = _owned_notification(notification_id)
    get_db()["notifications"].update_one(
        query, {"$set": {"read_at": datetime.utcnow()}}
    )
    return {"ok": True, "url": notification.get("url", url_for("habit_dashboard"))}


@app.route("/api/notifications/read-all", methods=["POST"])
def mark_all_notifications_read():
    if not session.get("user_id") or not session.get("user_email"):
        return {"error": "Login required."}, 401
    _validate_habit_form_token()
    get_db()["notifications"].update_many(
        {"user_email": session["user_email"], "read_at": {"$exists": False}},
        {"$set": {"read_at": datetime.utcnow()}},
    )
    return {"ok": True}


@app.route("/habits/<habit_id>/edit", methods=["GET", "POST"])
def edit_habit(habit_id):
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    errors = []
    form = {
        "name": habit.get("name", ""),
        "description": habit.get("description") or "",
        "target": str(habit.get("target", "")),
        "frequency": habit.get("frequency", ""),
        "reminder_enabled": habit.get("reminder_enabled", False),
        "reminder_time": habit.get("reminder_time") or "",
        "reminder_timezone": habit.get("reminder_timezone") or "UTC",
    }

    if request.method == "POST":
        _validate_habit_form_token()
        form = {
            key: request.form.get(key, "").strip()
            for key in form
        }
        form["reminder_enabled"] = request.form.get("reminder_enabled") == "on"

        if not form["name"]:
            errors.append("Enter a name for your habit.")
        elif len(form["name"]) > 100:
            errors.append("Habit name must be 100 characters or fewer.")

        try:
            target = int(form["target"])
        except ValueError:
            target = 0

        if target < 1:
            errors.append("Target must be a whole number of 1 or more.")

        if form["frequency"] not in FREQUENCY_CHOICES:
            errors.append("Choose a frequency.")
        if form["reminder_enabled"]:
            try:
                datetime.strptime(form["reminder_time"], "%H:%M")
            except ValueError:
                errors.append("Enter a valid reminder time.")
            if not _is_valid_timezone(form["reminder_timezone"]):
                errors.append("Choose a valid time zone.")

        if not errors:
            result = get_db()["habits"].update_one(
                query,
                {
                    "$set": {
                        "name": form["name"],
                        "description": form["description"] or None,
                        "target": target,
                        "frequency": form["frequency"],
                        "reminder_enabled": form["reminder_enabled"],
                        "reminder_time": form["reminder_time"] or None,
                        "reminder_timezone": form["reminder_timezone"] or "UTC",
                    }
                },
            )
            if result.matched_count == 0:
                abort(404)
            return redirect(url_for("habit_dashboard"))

    return render_template(
        "apps/habit_tracker/index.html",
        page_id="habit-tracker",
        form=form,
        errors=errors,
        frequency_choices=FREQUENCY_CHOICES,
        editing=True,
        habit_id=habit_id,
        habit_csrf_token=_habit_form_token(),
    ), (400 if errors else 200)


@app.route("/habits/<habit_id>/delete", methods=["GET", "POST"])
def delete_habit(habit_id):
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)

    if request.method == "POST":
        _validate_habit_form_token()
        result = get_db()["habits"].delete_one(query)
        if result.deleted_count == 0:
            abort(404)
        return redirect(url_for("habit_dashboard"))

    return render_template(
        "apps/habit_tracker/delete.html",
        habit=habit,
        habit_id=habit_id,
        habit_csrf_token=_habit_form_token(),
    )


@app.route("/account/delete-data", methods=["GET", "POST"])
def delete_account_data():
    """Wipe the user's habits (and the completion history stored on them) but keep the account."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query = {"user_email": session["user_email"]}

    if request.method == "POST":
        _validate_habit_form_token()
        get_db()["habits"].delete_many(query)
        flash("All of your habit data has been deleted.", "success")
        return redirect(url_for("habit_dashboard"))

    return render_template(
        "account/delete_data.html",
        habit_count=get_db()["habits"].count_documents(query),
        habit_csrf_token=_habit_form_token(),
    )


if __name__ == "__main__":
    app.run(debug=True)
