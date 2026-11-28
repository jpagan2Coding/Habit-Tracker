import csv
import io
import os
import re
import secrets
import sqlite3
import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache, wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pymongo import ASCENDING, MongoClient
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import certifi
from bson import ObjectId
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from models import FREQUENCY_CHOICES, HABIT_FILE_FIELDS, PAUSE_DURATIONS, WHEN_MISSED_CHOICES
from passcode import (
    PASSCODE_MAX_LENGTH,
    PASSCODE_MIN_LENGTH,
    PASSCODE_RULE,
    LocalPasscodeStore,
    validate_new_passcode,
)
from progress import (
    VALID_PROGRESS_PERIODS,
    build_habit_history,
    calculate_overall_progress,
    completion_count_on,
    current_break,
    current_period_progress,
    habit_streak,
    habit_summary,
    period_start,
    target_on,
    goal_increase_suggestion,
)
from reminders import format_reminder_schedule, validate_reminder_settings


def _is_valid_timezone(name):
    if name == "UTC":
        return True
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


def _validate_habit_form(
    form, *, target_required=False, max_name_length=None, allow_legacy_schedule=False
):
    errors = []

    if not form["name"]:
        errors.append("Enter a name for your habit.")
    elif max_name_length is not None and len(form["name"]) > max_name_length:
        errors.append(f"Habit name must be {max_name_length} characters or fewer.")

    target = None
    if target_required:
        if not form["target"]:
            errors.append("Enter a target.")
        elif not form["target"].isdigit() or int(form["target"]) < 1:
            errors.append("Target must be a whole number of 1 or more.")
        else:
            target = int(form["target"])
    else:
        try:
            target = int(form["target"])
        except ValueError:
            target = 0

        if target < 1:
            errors.append("Target must be a whole number of 1 or more.")

    if form["frequency"] not in FREQUENCY_CHOICES:
        errors.append("Choose a frequency.")
    reminder_errors, weekdays, month_day = validate_reminder_settings(
        form["frequency"],
        form["reminder_enabled"],
        form["reminder_time"],
        form["reminder_timezone"],
        form.get("reminder_weekdays"),
        form.get("reminder_month_day"),
        allow_legacy_schedule=allow_legacy_schedule,
    )
    errors.extend(reminder_errors)
    form["reminder_weekdays"] = weekdays
    form["reminder_month_day"] = month_day

    return errors, target


def _parse_start_date(value, today, current=None):
    """Check the start date from the habit form (US-23).

    Returns (start_date, error). A blank field keeps the current start date,
    and keeping a habit's existing start date is always allowed, even after
    that date has passed. A new start date has to be today or later.
    """
    if not value or value == current:
        return current, None

    try:
        start = date.fromisoformat(value)
    except ValueError:
        return current, "Enter a valid start date."

    if start < today:
        return current, "Start date cannot be in the past."

    return start.isoformat(), None

def _parse_when_missed(value):
    """Check the "If I miss it" choice from the habit form (US-24).

    Returns (when_missed, error). A blank choice means "fail", which is how
    habits worked before this setting existed.
    """
    if not value:
        return "fail", None

    if value not in WHEN_MISSED_CHOICES:
        return "fail", "Choose what happens when this habit is missed."

    return value, None


load_dotenv(Path(__file__).resolve().with_name("atlas-credentials.env"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
app.config["DEV_TIME_TRAVEL_ENABLED"] = os.getenv(
    "DEV_TIME_TRAVEL_ENABLED", ""
).strip().lower() in {"1", "true", "yes", "on"}
# App passcode hashes stay on this machine (instance/ is git-ignored), never in MongoDB.
app.config["PASSCODE_DB_PATH"] = os.getenv("PASSCODE_DB_PATH") or os.path.join(
    app.instance_path, "local_passcodes.sqlite3"
)

DEV_CLOCK_SESSION_KEY = "dev_clock_date"


def dev_time_travel_enabled():
    return app.debug or app.config["DEV_TIME_TRAVEL_ENABLED"]


def _utc_now():
    return datetime.now(timezone.utc)


def current_app_datetime():
    """Return UTC now with the session's developer clock offset when enabled."""
    now = _utc_now()
    if not dev_time_travel_enabled():
        return now

    clock_setting = session.get(DEV_CLOCK_SESSION_KEY)
    if clock_setting is None:
        return now
    try:
        if isinstance(clock_setting, (int, float)):
            return now + timedelta(seconds=clock_setting)
        if len(clock_setting) == 10:
            simulated_date = date.fromisoformat(clock_setting)
            return now.replace(
                year=simulated_date.year,
                month=simulated_date.month,
                day=simulated_date.day,
            )
        simulated_datetime = datetime.fromisoformat(clock_setting)
        if simulated_datetime.tzinfo is None:
            simulated_datetime = simulated_datetime.replace(tzinfo=timezone.utc)
        simulated_datetime = simulated_datetime.astimezone(timezone.utc)
        session[DEV_CLOCK_SESSION_KEY] = (simulated_datetime - now).total_seconds()
        return simulated_datetime
    except (OverflowError, TypeError, ValueError):
        app.logger.warning("Ignoring invalid simulated date and time in the session.")
        session.pop(DEV_CLOCK_SESSION_KEY, None)
        return now


def current_app_date():
    return current_app_datetime().date()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id") or not session.get("user_email"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def _mongo_client_options(mongo_uri):
    """Connection options for MongoClient.

    Atlas requires TLS, and the python.org build of Python ships without a CA
    bundle, so certificate verification fails with CERTIFICATE_VERIFY_FAILED
    unless pymongo is pointed at certifi's bundle. Plain mongodb:// URIs (a local
    mongod) are left untouched because tlsCAFile would force TLS on.
    """
    options = {"serverSelectionTimeoutMS": 30000}
    lowered = mongo_uri.lower()
    if lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered:
        options["tlsCAFile"] = certifi.where()
    return options


@lru_cache(maxsize=1)
def _get_mongo_client(mongo_uri):
    return MongoClient(mongo_uri, **_mongo_client_options(mongo_uri))


def get_db():
    """Connect to the team's MongoDB database using MONGODB_URI."""
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        raise RuntimeError("MONGODB_URI is not configured")

    client = _get_mongo_client(mongo_uri)
    return client[os.getenv("MONGODB_DATABASE", "Habit-Tracker")]


def ensure_user_indexes(users):
    """Unique indexes so two registrations can never create the same email/username.

    create_index is idempotent. The username index is sparse because accounts
    seeded for the login story may not have a username field.
    """
    users.create_index([("email", ASCENDING)], unique=True, name="email_unique")
    users.create_index([("username", ASCENDING)], unique=True, sparse=True, name="username_unique")


@app.route("/")
def home():
    return render_template("home/index.html", page_id="home")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Render the login screen and authenticate an existing user."""
    if session.get("user_id") and session.get("user_email"):
        return redirect(url_for("habit_dashboard"))

    error = None
    email = ""

    if request.method == "POST":
        identifier = request.form.get("email", "").strip()
        email = identifier
        password = request.form.get("password", "")

        if not email or not password:
            error = "Enter both your email and password to continue."
        else:
            try:
                user = get_db()["users"].find_one(
                    {"$or": [{"email": identifier.lower()}, {"username": identifier}]}
                )
            except (OSError, PyMongoError, RuntimeError):
                user = None

            stored_password = user.get("password", "") if user else ""
            if user and check_password_hash(stored_password, password):
                session.clear()
                session["user_id"] = str(user.get("_id", ""))
                session["username"] = user.get("username", email)
                session["user_email"] = user.get("email", email).lower()
                return redirect(url_for("habit_dashboard"))

            error = "Invalid email or password."

    return render_template(
        "auth/login.html",
        page_id="login",
        error=error,
        email=email,
    )


@app.route("/habit-tracker", methods=["GET", "POST"])
@login_required
def habit_tracker():
    today = current_app_date()
    form = {
        "name": "",
        "description": "",
        "target": "",
        "frequency": "",
        "reminder_enabled": False,
        "reminder_time": "",
        "reminder_timezone": "UTC",
        "start_date": today.isoformat(),
        "when_missed": "fail",
        "reminder_weekdays": [],
        "reminder_month_day": "",
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
                "start_date": request.form.get("start_date", "").strip(),
                "when_missed": request.form.get("when_missed", "").strip(),
               "reminder_weekdays": request.form.getlist("reminder_weekdays"),
               "reminder_month_day": request.form.get("reminder_month_day", "").strip(),
           }
        )

        errors, target = _validate_habit_form(
           form,
           target_required=True,
           allow_legacy_schedule=(
               "reminder_weekdays" not in request.form
               and "reminder_month_day" not in request.form
           ),
        )
        start_date, start_date_error = _parse_start_date(form["start_date"], today)
        if start_date_error:
            errors.append(start_date_error)

        when_missed, when_missed_error = _parse_when_missed(form["when_missed"])
        if when_missed_error:
            errors.append(when_missed_error)

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
                    # a blank start date means the habit starts today
                    "start_date": start_date or today.isoformat(),
                    "when_missed": when_missed,
                    "reminder_weekdays": form["reminder_weekdays"],
                    "reminder_month_day": form["reminder_month_day"],
                }
            )
            return redirect(url_for("habit_dashboard"))

    return render_template(
        "apps/habit_tracker/index.html",
        page_id="habit-tracker",
        form=form,
        errors=errors,
        frequency_choices=FREQUENCY_CHOICES,
        today=today.isoformat(),
    ), (400 if errors else 200)


# Habits with these statuses don't show up on the "today" list
INACTIVE_STATUSES = ("paused", "archived", "expired")


def is_due_today(habit, today):
    """Determine whether a habit belongs in today's habit list."""
    if habit.get("status", "active") in INACTIVE_STATUSES:
        return False

    today_iso = today.isoformat()

    start_date = habit.get("start_date")
    if start_date and start_date > today_iso:
        return False

    today_count = completion_count_on(habit, today)
    progress = current_period_progress(habit, today)
    skipped_today = today_iso in habit.get("skipped_dates", [])

    if current_break(habit, today):
        return today_count > 0

    # Retain completed and skipped habits so users see their status.
    if today_count > 0 or skipped_today:
        return True

    # Continue showing habits until the period target is reached.
    return progress["completed"] < progress["target"]

def _previous_period(frequency, today):
    """Return the first and last day of the period before the current one"""
    current_start = period_start(frequency, today)
    previous_end = current_start - timedelta(days=1)
    previous_start = period_start(frequency, previous_end)
    return previous_start, previous_end


def _habit_start(habit):
    """The day a habit started: its start date, or the day it was created"""
    start_date = habit.get("start_date")
    if start_date:
        return date.fromisoformat(start_date)

    created_at = habit.get("created_at")
    if isinstance(created_at, datetime):
        return created_at.date()

    return None


def _validate_completion_date(value, habit, today):
    """Validate a selected completion date without modifying the habit."""
    try:
        selected = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None, "Choose a valid completion date."

    if selected.isoformat() != value:
        return None, "Completion dates must use YYYY-MM-DD."

    if selected > today:
        return None, "Completion date cannot be in the future."

    start = _habit_start(habit)
    if start is not None and selected < start:
        return None, "Completion date cannot be before the habit starts."

    if habit.get("status", "active") in INACTIVE_STATUSES:
        return None, "This habit is inactive."

    if current_break(habit, selected):
        return None, "This habit was on a break on the selected date."

    return selected, None


def is_overdue(habit, today):
    """Check if a habit set to "overdue" was missed last period and still isn't done"""
    if habit.get("when_missed") != "overdue":
        return False

    # on a break, paused, archived, expired, or not started yet
    if not is_due_today(habit, today):
        return False

    frequency = habit.get("frequency") or "daily"
    if frequency == "daily":
        today_iso = today.isoformat()

        if today_iso in (habit.get("skipped_dates") or []):
            return False

        if completion_count_on(habit, today) >= target_on(habit, today):
            return False

        yesterday = today - timedelta(days=1)
        habit_start = _habit_start(habit)

        if habit_start is not None and yesterday < habit_start:
            return False

        if current_break(habit, yesterday):
            return False

        yesterday_count = completion_count_on(habit, yesterday)
        yesterday_skipped = yesterday.isoformat() in (
            habit.get("skipped_dates") or []
        )

        if yesterday_skipped and yesterday_count == 0:
            return False

        return yesterday_count < target_on(habit, yesterday)
    today_iso = today.isoformat()
    completed = {d for d in habit.get("completed_dates", []) if isinstance(d, str)}
    skipped = {d for d in habit.get("skipped_dates", []) if isinstance(d, str)}

    # completing it this period (or skipping it today) clears the overdue mark
    current_start_iso = period_start(frequency, today).isoformat()
    if any(current_start_iso <= d <= today_iso for d in completed):
        return False
    if today_iso in skipped:
        return False

    previous_start, previous_end = _previous_period(frequency, today)

    # a habit can't be overdue for a period from before it started
    habit_start = _habit_start(habit)
    if habit_start and habit_start > previous_start:
        return False

    # count last period's check-ins, plus the days that were skipped or on a break, since those count as excused instead of missed
    done_last_period = 0
    excused_last_period = 0
    day = previous_start
    while day <= previous_end:
        day_iso = day.isoformat()
        if day_iso in completed:
            done_last_period += 1
        elif day_iso in skipped or current_break(habit, day):
            excused_last_period += 1
        day += timedelta(days=1)

    # a daily habit can only be checked in once a day, so one is enough
    needed = 1 if frequency == "daily" else (habit.get("target") or 1)
    return done_last_period + excused_last_period < needed


def _resume_date(habit, today):
    """First day a habit on a break is active again, or None when it isn't on a break."""
    habit_break = current_break(habit, today)
    if habit_break is None:
        return None
    return date.fromisoformat(habit_break["end"]) + timedelta(days=1)


@app.route("/dashboard")
@login_required
def habit_dashboard():
    habits = list(
        get_db()["habits"].find({"user_email": session.get("user_email")}).sort("created_at", -1)
    )
    for habit in habits:
        habit["id"] = str(habit["_id"])
        habit["reminder_description"] = format_reminder_schedule(habit)

    today_date = current_app_date()
    today = today_date.isoformat()
    progress_period = _selected_progress_period()
    goal_suggestions = []
    for habit in habits:
        suggestion = goal_increase_suggestion(habit, today_date)
        if suggestion is not None:
            goal_suggestions.append(
                {
                    "habit_id": habit["id"],
                    "habit_name": habit.get("name", ""),
                    **suggestion,
                }
            )

# show each habit's start date in All My Habits and flag ones that haven't started
    for habit in habits:
        start_date = habit.get("start_date")
        habit["upcoming"] = bool(start_date) and start_date > today
        if start_date:
            habit["start_label"] = date.fromisoformat(start_date).strftime("%B %d, %Y")

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

    # habits missed last time that carry over get an Overdue badge and go first
    for habit in todays_habits:
        habit["overdue"] = is_overdue(habit, today_date)
    todays_habits.sort(key=lambda habit: not habit["overdue"])

    progress_by_id = {
        habit["id"]: current_period_progress(habit, today_date)
        for habit in habits
    }

    today_count_by_id = {
        habit["id"]: completion_count_on(habit, today_date)
        for habit in habits
    }

    completion_by_id = {
        habit_id: progress["completed"] >= progress["target"]
        for habit_id, progress in progress_by_id.items()
    }

    skipped_by_id = {habit["id"]: today in habit.get("skipped_dates", []) for habit in habits}

    resume_by_id = {habit["id"]: _resume_date(habit, today_date) for habit in habits}
    on_break_count = sum(1 for resume_date in resume_by_id.values() if resume_date)

    completed_today = sum(completion_by_id[habit["id"]] for habit in todays_habits)
    skipped_today = sum(skipped_by_id[habit["id"]] for habit in todays_habits)

    # Skipped habits are neutral: they drop out of today's progress instead of lowering it
    total_habits = len(habits)
    scheduled_today = len(todays_habits) - skipped_today

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
    reminded_habit_ids = {
        str(notification["habit_id"])
        for notification in notifications
        if (
            not notification.get("read_at")
            and notification.get("habit_id")
            and not any(
                habit["id"] == str(notification["habit_id"])
                and (
                    today in habit.get("completed_dates", [])
                    or today in habit.get("skipped_dates", [])
                )
                for habit in habits
            )
        )
    }

    return render_template(
        "apps/habit_tracker/dashboard.html",
        page_id="dashboard",
        habits=habits,
        todays_habits=todays_habits,
        goal_suggestions=goal_suggestions,
        completion_by_id=completion_by_id,
        progress_by_id=progress_by_id,
        today_count_by_id=today_count_by_id,
        skipped_by_id=skipped_by_id,
        resume_by_id=resume_by_id,
        on_break_count=on_break_count,
        total_habits=total_habits,
        completed_today=completed_today,
        skipped_today=skipped_today,
        scheduled_today=scheduled_today,
        completion_percentage=completion_percentage,
        overall_progress=overall_progress,
        progress_periods=VALID_PROGRESS_PERIODS,
        # One slide per habit in the Current Streak panel, in the same order as All My Habits.
        streak_slides=[
            {"habit": habit, **habit_streak(habit, today_date)} for habit in habits
        ],
        today=today,
        habit_csrf_token=_habit_form_token(),
        notifications=notifications,
        unread_notification_count=unread_notification_count,
        reminded_habit_ids=reminded_habit_ids,
    )


def _selected_progress_period():
    """The ?period= day count chosen on the page, falling back to the last 7 days."""
    try:
        period = int(request.args.get("period", 7))
    except ValueError:
        return 7
    return period if period in VALID_PROGRESS_PERIODS else 7


@app.route("/summary")
@login_required
def summary():
    """Per-habit streaks and completion rate over the selected period."""
    habits = list(
        get_db()["habits"].find({"user_email": session.get("user_email")}).sort("created_at", -1)
    )
    today = current_app_date()
    period_days = _selected_progress_period()

    return render_template(
        "apps/habit_tracker/summary.html",
        page_id="summary",
        habit_summaries=[(habit, habit_summary(habit, today, period_days)) for habit in habits],
        period_days=period_days,
        progress_periods=VALID_PROGRESS_PERIODS,
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
        username_error = _validate_username(username)
        if not username_error and (not username or not email or not password):
            error = "All fields are required."
        elif username_error:
            error = username_error
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

    return render_template("auth/register.html", page_id="register", username=username, email=email)


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


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_username(username):
    if not username:
        return "Enter a username."
    if len(username) > 80:
        return "Username must be 80 characters or fewer."
    if not USERNAME_PATTERN.fullmatch(username):
        return "Username may contain only letters, numbers, underscores, and hyphens."
    return None


def _authenticated_user(users):
    user_email = session.get("user_email")
    user_id = session.get("user_id")
    user_filter = {"email": user_email}
    if user_id and ObjectId.is_valid(user_id):
        user_filter = {"$or": [{"_id": ObjectId(user_id)}, {"email": user_email}]}
    return users.find_one(user_filter)


# text size options (cookie value -> label shown on the settings page)
TEXT_SIZES = {
    "small": "Small",
    "default": "Default",
    "large": "Large",
    "x-large": "Extra large",
}
TEXT_SIZE_COOKIE = "text_size"


def current_text_size():
    size = request.cookies.get(TEXT_SIZE_COOKIE, "default")
    return size if size in TEXT_SIZES else "default"


@app.context_processor
def inject_text_size():
    # makes text_size available on every page so <html> can use it
    return {"text_size": current_text_size()}


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        if request.form.get("action") == "save_settings":
            return _save_settings()
        if request.form.get("action") in {"update_username", "username"}:
            return _update_username()
        if request.form.get("action") in {"change_password", "password"}:
            return _change_password()
        if "new_username" in request.form:
            return _update_username()
        if "new_password" in request.form or "current_password" in request.form:
            return _change_password()

        size = request.form.get("text_size", "default")
        if request.form.get("reset") or size not in TEXT_SIZES:
            size = "default"

        response = redirect(url_for("settings"))
        if size == "default":
            response.delete_cookie(TEXT_SIZE_COOKIE)
        else:
            # remember the choice in this browser for a year
            response.set_cookie(TEXT_SIZE_COOKIE, size, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return response

    users = get_db()["users"]
    user = _authenticated_user(users)
    current_username = user.get("username", session.get("username", "")) if user else session.get("username", "")
    current_email = user.get("email", session.get("user_email", "")) if user else session.get("user_email", "")

    return render_template(
        "settings.html",
        page_id="settings",
        text_sizes=TEXT_SIZES,
        current_username=current_username,
        current_email=current_email,
        dev_time_travel_enabled=dev_time_travel_enabled(),
        simulated_datetime=current_app_datetime().strftime("%Y-%m-%dT%H:%M"),
        habit_csrf_token=_habit_form_token(),
    )


@app.route("/settings/username", methods=["POST"])
@login_required
def update_username():
    return _update_username()


@app.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    return _change_password()


def _save_settings():
    _validate_habit_form_token()
    users = get_db()["users"]
    user = _authenticated_user(users)
    if user is None:
        flash("Your account could not be found.", "error")
        return redirect(url_for("settings"))

    new_username = request.form.get("new_username", "").strip()
    username_error = _validate_username(new_username)
    if username_error is None:
        for existing in users.find({"username": new_username}):
            if str(existing.get("_id")) != str(user.get("_id")):
                username_error = "Username is already taken. Please choose another."
                break

    password_error = None
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    password_requested = bool(new_password or confirm_password)

    if password_requested:
        if not current_password:
            password_error = "Enter your current password."
        elif not new_password:
            password_error = "Enter a new password."
        elif not confirm_password:
            password_error = "Confirm your new password."
        elif len(new_password) < 8:
            password_error = "New password must be at least 8 characters long."
        elif new_password != confirm_password:
            password_error = "Passwords do not match."
        elif not check_password_hash(user.get("password", ""), current_password):
            password_error = "Current password is incorrect."

    if username_error:
        flash(username_error, "error")
        return redirect(url_for("settings"))
    if password_error:
        flash(password_error, "error")
        return redirect(url_for("settings"))

    updates = {"username": new_username}
    if password_requested:
        updates["password"] = generate_password_hash(new_password)

    try:
        ensure_user_indexes(users)
        result = users.update_one({"_id": user["_id"]}, {"$set": updates})
    except DuplicateKeyError:
        flash("Username is already taken. Please choose another.", "error")
        return redirect(url_for("settings"))
    except (OSError, PyMongoError, RuntimeError) as e:
        app.logger.error(f"Database error while saving settings: {e}")
        flash("Database error occurred while saving settings.", "error")
        return redirect(url_for("settings"))

    if result.matched_count == 0:
        flash("Your account could not be found.", "error")
        return redirect(url_for("settings"))

    session["username"] = new_username
    size = request.form.get("text_size", "default")
    if request.form.get("reset") or size not in TEXT_SIZES:
        size = "default"

    response = redirect(url_for("settings"))
    if size == "default":
        response.delete_cookie(TEXT_SIZE_COOKIE)
    else:
        response.set_cookie(
            TEXT_SIZE_COOKIE,
            size,
            max_age=60 * 60 * 24 * 365,
            samesite="Lax",
        )

    flash("Your settings and preferences saved successfully", "success")
    return response


def _update_username():
    _validate_habit_form_token()
    new_username = request.form.get("new_username", request.form.get("username", "")).strip()

    error = _validate_username(new_username)
    users = get_db()["users"]
    user = _authenticated_user(users)
    if user is None:
        error = "Your account could not be found."
    elif error is None:
        for existing in users.find({"username": new_username}):
            if str(existing.get("_id")) != str(user.get("_id")):
                error = "Username is already taken. Please choose another."
                break

    if error:
        flash(error, "error")
        return redirect(url_for("settings"))

    try:
        ensure_user_indexes(users)
        result = users.update_one(
            {"_id": user["_id"]},
            {"$set": {"username": new_username}},
        )
    except DuplicateKeyError:
        flash("Username is already taken. Please choose another.", "error")
        return redirect(url_for("settings"))
    except (OSError, PyMongoError, RuntimeError) as e:
        app.logger.error(f"Database error while updating username: {e}")
        flash("Database error occurred while updating username.", "error")
        return redirect(url_for("settings"))

    if result.matched_count == 0:
        flash("Your account could not be found.", "error")
        return redirect(url_for("settings"))

    session["username"] = new_username
    flash("Username updated successfully.", "success")
    return redirect(url_for("settings"))


def _change_password():
    _validate_habit_form_token()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get(
        "confirm_password",
        request.form.get("confirm_new_password", ""),
    )

    if not current_password:
        error = "Enter your current password."
    elif not new_password:
        error = "Enter a new password."
    elif not confirm_password:
        error = "Confirm your new password."
    elif len(new_password) < 8:
        error = "New password must be at least 8 characters long."
    elif new_password != confirm_password:
        error = "Passwords do not match."
    else:
        error = None

    users = get_db()["users"]
    user = _authenticated_user(users)
    if error is None:
        if user is None:
            error = "Your account could not be found."
        elif not check_password_hash(user.get("password", ""), current_password):
            error = "Current password is incorrect."

    if error:
        flash(error, "error")
        return redirect(url_for("settings"))

    try:
        result = users.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": generate_password_hash(new_password)}},
        )
    except (OSError, PyMongoError, RuntimeError) as e:
        app.logger.error(f"Database error while changing password: {e}")
        flash("Database error occurred while changing password.", "error")
        return redirect(url_for("settings"))

    if result.matched_count == 0:
        flash("Your account could not be found.", "error")
        return redirect(url_for("settings"))

    flash("Password changed successfully.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/dev-clock", methods=["POST"])
def set_dev_clock():
    if not dev_time_travel_enabled():
        abort(404)

    _validate_habit_form_token()
    if request.form.get("reset"):
        session.pop(DEV_CLOCK_SESSION_KEY, None)
        flash("The actual date and time are restored.", "success")
    else:
        submitted_datetime = request.form.get("simulated_datetime", "").strip()
        try:
            selected_datetime = datetime.fromisoformat(submitted_datetime)
        except ValueError:
            flash("Choose a valid date and time.", "error")
        else:
            if selected_datetime.tzinfo is None:
                selected_datetime = selected_datetime.replace(tzinfo=timezone.utc)
            selected_datetime = selected_datetime.astimezone(timezone.utc)
            session[DEV_CLOCK_SESSION_KEY] = (selected_datetime - _utc_now()).total_seconds()
            flash(
                f"The app date and time are now "
                f"{selected_datetime.strftime('%Y-%m-%d %H:%M UTC')}.",
                "success",
            )
    return redirect(url_for("settings"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))  # change to login once implemented


# --- Local app passcode -------------------------------------------------------
# A signed-in session stays locked until the user's local passcode is entered.
# Logging in clears the session, so every new sign-in asks for the passcode again.
PASSCODE_UNLOCKED_SESSION_KEY = "passcode_unlocked"
# Pages a locked session may still reach: the lock screen itself, log out, and assets.
PASSCODE_EXEMPT_ENDPOINTS = {"static", "unlock_app", "logout"}
# Endpoints whose callers (fetch on the dashboard) expect JSON rather than a redirect.
PASSCODE_JSON_ENDPOINTS = {"complete_habit", "uncomplete_habit"}
APP_LOCKED_MESSAGE = "The app is locked. Enter your passcode to continue."


def passcode_store():
    return LocalPasscodeStore(app.config["PASSCODE_DB_PATH"])


def _session_is_locked():
    user_id = session.get("user_id")
    if not user_id or session.get(PASSCODE_UNLOCKED_SESSION_KEY):
        return False
    return passcode_store().has_passcode(user_id)


def _safe_next_path(value):
    """Only allow redirects back to a path on this site after unlocking."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value


def _wants_json_response():
    return (
        request.path.startswith("/api/")
        or request.endpoint in PASSCODE_JSON_ENDPOINTS
        or request.is_json
        or request.accept_mimetypes.best == "application/json"
    )


@app.before_request
def require_app_passcode():
    """Keep habit information hidden until a locked session enters its passcode."""
    if request.endpoint in PASSCODE_EXEMPT_ENDPOINTS:
        return None

    try:
        locked = _session_is_locked()
    except sqlite3.Error:
        # Fail closed: without the store we can't tell whether this user set a passcode.
        app.logger.error("Could not read the local passcode store.")
        abort(503, description="Passcode storage is unavailable. Please try again later.")

    if not locked:
        return None

    if _wants_json_response():
        return jsonify({"error": APP_LOCKED_MESSAGE, "unlock_url": url_for("unlock_app")}), 401

    next_path = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("unlock_app", next=next_path))


@app.context_processor
def inject_passcode_state():
    enabled = False
    user_id = session.get("user_id")
    if user_id:
        try:
            enabled = passcode_store().has_passcode(user_id)
        except sqlite3.Error:
            app.logger.error("Could not read the local passcode store.")
    return {
        "app_passcode_enabled": enabled,
        "passcode_min_length": PASSCODE_MIN_LENGTH,
        "passcode_max_length": PASSCODE_MAX_LENGTH,
        "passcode_rule": PASSCODE_RULE,
    }


@app.route("/unlock", methods=["GET", "POST"])
def unlock_app():
    """Lock screen: ask for the local passcode before showing any habit information."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    user_id = session["user_id"]
    store = passcode_store()
    next_path = _safe_next_path(request.values.get("next"))

    if session.get(PASSCODE_UNLOCKED_SESSION_KEY) or not store.has_passcode(user_id):
        return redirect(next_path or url_for("habit_dashboard"))

    error = None
    reset_error = None
    status = 200

    if request.method == "POST":
        _validate_habit_form_token()

        if request.form.get("action") == "reset":
            # Forgotten passcode: the account password proves ownership and clears it.
            account_password = request.form.get("account_password", "")
            if not account_password:
                reset_error, status = "Enter your account password.", 400
            else:
                try:
                    user = _authenticated_user(get_db()["users"])
                except (OSError, PyMongoError, RuntimeError):
                    app.logger.error("Database error while resetting the app passcode.")
                    user = None
                if user and check_password_hash(user.get("password", ""), account_password):
                    store.remove(user_id)
                    session.pop(PASSCODE_UNLOCKED_SESSION_KEY, None)
                    flash(
                        "Your passcode was removed. You can create a new one in Settings.",
                        "success",
                    )
                    return redirect(url_for("settings", _anchor="passcode-heading"))
                reset_error, status = "Incorrect account password.", 401
        else:
            passcode = request.form.get("passcode", "")
            if not passcode:
                error, status = "Enter your passcode.", 400
            elif store.verify(user_id, passcode):
                session[PASSCODE_UNLOCKED_SESSION_KEY] = True
                return redirect(next_path or url_for("habit_dashboard"))
            else:
                error, status = "Incorrect passcode. Please try again.", 401

    return render_template(
        "auth/unlock.html",
        page_id="unlock",
        error=error,
        reset_error=reset_error,
        next_path=next_path or "",
        habit_csrf_token=_habit_form_token(),
    ), status


@app.route("/lock", methods=["POST"])
@login_required
def lock_app():
    """Lock the current session right away (only meaningful once a passcode exists)."""
    if not passcode_store().has_passcode(session["user_id"]):
        flash("Create a passcode in Settings before locking the app.", "error")
        return redirect(url_for("settings", _anchor="passcode-heading"))

    session.pop(PASSCODE_UNLOCKED_SESSION_KEY, None)
    return redirect(url_for("unlock_app"))


@app.route("/settings/passcode", methods=["POST"])
@login_required
def manage_passcode():
    """Create, change, or remove the local app passcode."""
    _validate_habit_form_token()

    user_id = session["user_id"]
    store = passcode_store()
    action = request.form.get("action", "")
    current_passcode = request.form.get("current_passcode", "")
    new_passcode = request.form.get("new_passcode", "")
    confirm_passcode = request.form.get("confirm_passcode", "")
    has_passcode = store.has_passcode(user_id)

    if action == "create":
        if has_passcode:
            error = "A passcode is already set. Use Change passcode to replace it."
        else:
            error = validate_new_passcode(new_passcode, confirm_passcode)
        success = "Passcode created. You'll need it to open the app each time you sign in."
    elif action in {"change", "remove"}:
        if not has_passcode:
            error = "You don't have a passcode yet."
        elif not current_passcode:
            error = "Enter your current passcode."
        elif not store.verify(user_id, current_passcode):
            error = "Current passcode is incorrect."
        elif action == "change":
            error = validate_new_passcode(new_passcode, confirm_passcode)
        else:
            error = None
        success = (
            "Passcode changed successfully."
            if action == "change"
            else "Passcode removed. The app will no longer ask for a passcode."
        )
    else:
        abort(400, description="Unknown passcode action.")

    settings_url = url_for("settings", _anchor="passcode-heading")
    if error:
        flash(error, "error")
        return redirect(settings_url)

    try:
        if action == "remove":
            store.remove(user_id)
        else:
            store.set(user_id, new_passcode)
    except (OSError, sqlite3.Error):
        # Never include the submitted passcode in logs.
        app.logger.error("Could not update the local passcode store.")
        flash("Your passcode could not be saved. Please try again.", "error")
        return redirect(settings_url)

    if action == "remove":
        session.pop(PASSCODE_UNLOCKED_SESSION_KEY, None)
    else:
        # The person who just set the passcode doesn't need to unlock right away.
        session[PASSCODE_UNLOCKED_SESSION_KEY] = True

    flash(success, "success")
    return redirect(settings_url)


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


@app.route("/api/push-status")
def push_status():
    if not session.get("user_id") or not session.get("user_email"):
        return {"error": "Login required."}, 401
    configured = all(
        os.getenv(name) for name in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT")
    )
    subscription = get_db()["push_subscriptions"].find_one(
        {"user_email": session["user_email"]},
        {"_id": 1},
    )
    return {"configured": configured, "subscribed": subscription is not None}


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


def _owned_document(collection_name, document_id):
    if not ObjectId.is_valid(document_id):
        abort(404)

    query = {
        "_id": ObjectId(document_id),
        "user_email": session["user_email"],
    }
    document = get_db()[collection_name].find_one(query)
    if document is None:
        abort(404)
    return query, document


def _owned_habit(habit_id):
    return _owned_document("habits", habit_id)

@app.route("/habits/<habit_id>/history")
@login_required
def habit_history(habit_id):
    """Display the completion history of an owned habit."""
    _, habit = _owned_habit(habit_id)
    today = current_app_date()

    history = build_habit_history(
        habit,
        today,
        overdue=is_overdue(habit, today),
    )

    period = request.args.get("period", "all")

    if period not in ("all", "7", "30", "90"):
        period = "all"

    if period != "all":
        start_date = today - timedelta(days=int(period) - 1)
        history = [
            entry for entry in history
            if entry["end"] >= start_date
        ]

    return render_template(
        "apps/habit_tracker/history.html",
        habit=habit,
        current_target=target_on(habit, today),
        streak=habit_streak(habit, today),
        habit_id=habit_id,
        history=history,
        selected_period=period,
        today=today,
    )
def _owned_notification(notification_id):
    return _owned_document("notifications", notification_id)


@app.route("/api/notifications/<notification_id>/read", methods=["POST"])
def mark_notification_read(notification_id):
    if not session.get("user_id") or not session.get("user_email"):
        return {"error": "Login required."}, 401
    _validate_habit_form_token()
    query, notification = _owned_notification(notification_id)
    get_db()["notifications"].update_one(query, {"$set": {"read_at": datetime.utcnow()}})
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

def _update_completion_count(query, habit, today, new_count, *, submission_id=None):
    """Persist check-ins for a date without losing older history."""
    today_str = today.isoformat()
    old_history = habit.get("completion_history") or []

    history = [
        record.copy() if isinstance(record, dict) else record
        for record in old_history
    ]

    updated = False

    for record in history:
        if isinstance(record, dict) and str(record.get("date"))[:10] == today_str:
            record["status"] = "completed" if new_count else "undone"
            record["count"] = new_count
            updated = True
            break

    if not updated:
        history.append({
            "date": today_str,
            "status": "completed" if new_count else "undone",
            "count": new_count,
        })

    expected = (
        {"completion_history": old_history}
        if "completion_history" in habit
        else {"completion_history": {"$exists": False}}
    )

    update = {
        "$set": {"completion_history": history},
    }

    if new_count > 0:
        update["$addToSet"] = {"completed_dates": today_str}
        update["$pull"] = {"skipped_dates": today_str}
    else:
        update["$pull"] = {"completed_dates": today_str}

    if submission_id is not None:
        query = {
            **query,
            "completion_submission_ids": {"$ne": submission_id},
        }
        update.setdefault("$addToSet", {})[
            "completion_submission_ids"
        ] = submission_id

    result = get_db()["habits"].update_one(
        {**query, **expected},
        update,
    )

    return result.modified_count == 1


def _completion_form_signer():
    return URLSafeTimedSerializer(
        app.config["SECRET_KEY"],
        salt="us38-completion-form",
    )


def _completion_period_cutoff(habit, selected, today):
    """Include later check-ins in the selected week or month."""
    frequency = habit.get("frequency") or "daily"
    start = period_start(frequency, selected)

    if frequency == "weekly":
        end = start + timedelta(days=6)
    elif frequency == "monthly":
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
    else:
        end = selected

    return min(end, today)


@app.route("/habits/<habit_id>/record-completion", methods=["GET", "POST"])
@login_required
def record_habit_completion(habit_id):
    """Record one check-in on a user-selected date."""
    query, habit = _owned_habit(habit_id)
    today = current_app_date()
    selected_value = today.isoformat()
    error = None
    status_code = 200
    signer = _completion_form_signer()

    if request.method == "POST":
        _validate_habit_form_token()
        selected_value = request.form.get("completion_date", "")
        submitted_token = request.form.get("submission_token", "")
        try:
            payload = signer.loads(submitted_token, max_age=3600)
        except (BadSignature, SignatureExpired):
            abort(400, description="This form expired or is invalid. Reload and try again.")

        if (
            not isinstance(payload, dict)
            or payload.get("habit_id") != habit_id
            or payload.get("user_email") != session["user_email"]
            or not isinstance(payload.get("submission_id"), str)
            or not payload["submission_id"]
        ):
            abort(400, description="Invalid completion form.")

        submission_id = payload["submission_id"]

        if submission_id in (habit.get("completion_submission_ids") or []):
            flash("This completion form has already been submitted.", "error")
            return redirect(url_for("habit_history", habit_id=habit_id))

        selected, error = _validate_completion_date(
            selected_value, habit, today
        )

        if error:
            status_code = 400
        else:
            cutoff = _completion_period_cutoff(habit, selected, today)
            progress = current_period_progress(habit, cutoff)

            if progress["completed"] >= progress["target"]:
                error = "The target for that day, week, or month is already reached."
                status_code = 409
            else:
                new_count = completion_count_on(habit, selected) + 1

                if _update_completion_count(
                    query,
                    habit,
                    selected,
                    new_count,
                    submission_id=submission_id,
                ):
                    flash(
                        f"One check-in recorded for {selected.isoformat()}.",
                        "success",
                    )
                    return redirect(url_for("habit_history", habit_id=habit_id))

                error = (
                    "The habit changed or this form was already submitted. "
                    "Review the history before trying again."
                )
                status_code = 409

    submission_token = signer.dumps(
        {
            "habit_id": habit_id,
            "user_email": session["user_email"],
            "submission_id": secrets.token_hex(24),
        }
    )
    start = _habit_start(habit)

    return render_template(
        "apps/habit_tracker/record_completion.html",
        habit=habit,
        habit_id=habit_id,
        today=today.isoformat(),
        earliest_date=start.isoformat() if start else None,
        selected_date=selected_value,
        error=error,
        habit_csrf_token=_habit_form_token(),
        submission_token=submission_token,
    ), status_code

@app.route("/habits/<habit_id>/complete", methods=["POST"])
def complete_habit(habit_id):
    """Record one check-in for an owned habit."""
    if not session.get("user_id") or not session.get("user_email"):
        return jsonify({"error": "Login required"}), 401

    _validate_habit_form_token()

    query, habit = _owned_habit(habit_id)
    today = current_app_date()
    today_str = today.isoformat()

    if habit.get("status", "active") in INACTIVE_STATUSES:
        return jsonify({"error": "Habit is inactive"}), 409

    if current_break(habit, today):
        return jsonify({"error": "Habit is on a break"}), 409

    if not is_due_today(habit, today):
        return jsonify({"error": "Habit is not due"}), 409

    progress = current_period_progress(habit, today)

    if progress["completed"] >= progress["target"]:
        return jsonify({"error": "Habit target already reached"}), 409

    current_count = completion_count_on(habit, today)
    new_count = current_count + 1

    if not _update_completion_count(query, habit, today, new_count):
        return jsonify({"error": "Please retry completion"}), 409

    period_completed = progress["completed"] + 1

    return jsonify({
        "habit_id": habit_id,
        "completed": period_completed >= progress["target"],
        "completed_date": today_str,
        "count": new_count,
        "period_completed": period_completed,
        "target": progress["target"],
        "message": "Habit check-in recorded",
    }), 200


@app.route("/habits/<habit_id>/uncomplete", methods=["POST"])
def uncomplete_habit(habit_id):
    """Undo one of today's check-ins for an owned habit."""
    if not session.get("user_id") or not session.get("user_email"):
        return jsonify({"error": "Login required"}), 401

    _validate_habit_form_token()

    query, habit = _owned_habit(habit_id)
    today = current_app_date()
    today_str = today.isoformat()

    current_count = completion_count_on(habit, today)

    if current_count <= 0:
        return jsonify({"error": "No completion to undo today"}), 409

    progress = current_period_progress(habit, today)
    new_count = current_count - 1

    if not _update_completion_count(query, habit, today, new_count):
        return jsonify({"error": "Please retry undo"}), 409

    period_completed = max(progress["completed"] - 1, 0)

    return jsonify({
        "habit_id": habit_id,
        "completed": period_completed >= progress["target"],
        "completed_date": today_str,
        "count": new_count,
        "period_completed": period_completed,
        "target": progress["target"],
        "message": "Habit check-in undone",
    }), 200

def _parse_history_row(row):
    if row["completed_date"]:
        completed = date.fromisoformat(row["completed_date"])
        if completed.isoformat() != row["completed_date"]:
            raise ValueError("Completion dates must use YYYY-MM-DD.")
        status = row["status"] or "completed"
        if status not in ("completed", "skipped"):
            raise ValueError("History status must be completed or skipped.")
        return row["completed_date"], status
    if row["status"]:
        raise ValueError("A history status requires a completion date.")
    return None

def _parse_goal_settings(value, current_target, frequency):
    """Validate optional progressive-goal metadata from a habit backup."""
    try:
        settings = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid goal settings.") from error

    allowed = {
        "initial_target",
        "target_history",
        "start_date",
        "goal_suggestion_last_decision",
    }
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError("Invalid goal settings.")

    if frequency != "daily":
        raise ValueError("Goal settings require a daily habit.")

    initial_target = settings.get("initial_target")
    if type(initial_target) is not int or initial_target < 1:
        raise ValueError("Initial target must be a positive whole number.")

    history = settings.get("target_history")
    if not isinstance(history, list):
        raise ValueError("Goal settings require a target history list.")

    if not history and initial_target != current_target:
        raise ValueError(
            "Without target history, the initial target must match "
            "the habit target."
        )
    previous_date = None
    for change in history:
        if not isinstance(change, dict) or set(change) != {
            "effective_date", "target"
        }:
            raise ValueError("Invalid target history entry.")

        raw_date = change["effective_date"]
        if not isinstance(raw_date, str):
            raise ValueError("Target dates must use YYYY-MM-DD.")

        try:
            effective = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError("Invalid target effective date.") from error

        if effective.isoformat() != raw_date:
            raise ValueError("Target dates must use YYYY-MM-DD.")

        if previous_date is not None and effective <= previous_date:
            raise ValueError("Target history dates must be unique and ordered.")

        if type(change["target"]) is not int or change["target"] < 1:
            raise ValueError("History targets must be positive whole numbers.")

        previous_date = effective

    if history and history[-1]["target"] != current_target:
        raise ValueError("The latest history target must match the habit target.")

    for field in ("start_date", "goal_suggestion_last_decision"):
        if field not in settings:
            continue

        raw_date = settings[field]
        if not isinstance(raw_date, str):
            raise ValueError("Goal settings dates must use YYYY-MM-DD.")

        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError("Invalid goal settings date.") from error

        if parsed.isoformat() != raw_date:
            raise ValueError("Goal settings dates must use YYYY-MM-DD.")

    return settings

def _parse_habit_file(payload):
    """Validate a habit file before writing anything to the database."""
    reader = csv.DictReader(
        io.StringIO(payload.decode("utf-8-sig"), newline=""),
        strict=True,
    )
    has_goal_settings = reader.fieldnames == [
        *HABIT_FILE_FIELDS, "count", "goal_settings"
    ]
    has_counts = (
        reader.fieldnames == [*HABIT_FILE_FIELDS, "count"]
        or has_goal_settings
    )
    if reader.fieldnames != HABIT_FILE_FIELDS and not has_counts:
        raise ValueError(
            "The file must contain the supported habit columns in order."
        )

    details = None
    completed_dates = []
    skipped_dates = []
    completion_counts = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Every row must contain all habit columns.")
        current = {
            field: row[field]
            for field in HABIT_FILE_FIELDS
            if field not in ("completed_date", "status")
        }
        if has_goal_settings:
            current["goal_settings"] = row["goal_settings"]
        if details is not None and current != details:
            raise ValueError("All rows must describe the same habit.")
        details = current
        history = _parse_history_row(row)

        if history is None:
            if has_counts and row["count"] not in ("", "0"):
                raise ValueError("A completion count requires a date.")
            continue
        history_date, status = history
        if has_counts:
            raw_count = row["count"]
            if status == "completed":
                if not raw_count.isdigit() or int(raw_count) < 1:
                    raise ValueError(
                        "Completed history must have a positive count."
                    )
                count = int(raw_count)
            else:
                if raw_count not in ("", "0"):
                    raise ValueError(
                        "Skipped history cannot have completions."
                    )
                count = 0
            if history_date in completion_counts:
                previous = completion_counts[history_date]
                if previous != (status, count):
                    raise ValueError(
                        "Conflicting history entries for the same date."
                    )
            else:
                completion_counts[history_date] = (status, count)
        dates = (
            completed_dates
            if status == "completed"
            else skipped_dates
        )
        if history_date not in dates:
            dates.append(history_date)

    if details is None:
        raise ValueError("The file contains no habit.")
    if not details["name"].strip():
        raise ValueError("Enter a name for your habit.")
    if not details["target"].isdigit() or int(details["target"]) < 1:
        raise ValueError("Target must be a positive whole number.")
    if details["frequency"] not in FREQUENCY_CHOICES:
        raise ValueError("Choose a supported frequency.")
    created_at = details["created_at"]
    if created_at:
        created_at = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at = created_at.astimezone(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)
    habit = {
        "name": details["name"],
        "description": details["description"],
        "target": int(details["target"]),
        "frequency": details["frequency"],
        "created_at": created_at,
        "completed_dates": completed_dates,
        "skipped_dates": skipped_dates,
    }
    if has_counts:
        habit["completion_history"] = [
            {
                "date": history_date,
                "status": status,
                "count": count,
            }
            for history_date, (status, count)
            in completion_counts.items()
        ]
    if has_goal_settings:
        habit.update(
            _parse_goal_settings(
                details["goal_settings"],
                habit["target"],
                habit["frequency"],
            )
        )
    return habit

def _format_export_datetime(value):
    if not isinstance(value, datetime):
        return value or ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _serialize_habit_history(habit):
    history = [(history_date, "completed") for history_date in habit.get("completed_dates") or []]
    history.extend((history_date, "skipped") for history_date in habit.get("skipped_dates") or [])
    serialized = []
    for history_date, status in history:
        if isinstance(history_date, datetime):
            history_date = history_date.date().isoformat()
        serialized.append((history_date, status))
    return serialized or [("", "")]


@app.route("/habits/import", methods=["POST"])
def import_habit():
    """Import a .habit attachment as a new habit owned by the signed-in user."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))
    upload = request.files.get("file")
    if upload is None or not upload.filename or not upload.filename.lower().endswith(".habit"):
        abort(400, description="Choose a .habit file to import.")
    # Bound memory use while still allowing large completion histories.
    payload = upload.stream.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        abort(400, description="Habit files must be no larger than 1 MB.")
    try:
        habit = _parse_habit_file(payload)
    except (UnicodeError, ValueError, csv.Error) as error:
        abort(400, description=f"Invalid habit file: {error}")
    habit["user_email"] = session["user_email"]
    get_db()["habits"].insert_one(habit)
    flash("Habit imported successfully.", "success")
    return redirect(url_for("habit_dashboard"))


@app.route("/habits/<habit_id>/export")
def export_habit(habit_id):
    """Download an owned habit and its history as UTF-8 CSV."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    _, habit = _owned_habit(habit_id)
    created_at = _format_export_datetime(habit.get("created_at"))
    output = io.StringIO(newline="")
    history_rows = _serialize_habit_history(habit)

    has_goal_settings = (
        "initial_target" in habit
        or bool(habit.get("target_history"))
        or bool(habit.get("goal_suggestion_last_decision"))
    )
    has_multiple_checkins = any(
        status == "completed"
        and completion_count_on(
            habit, date.fromisoformat(history_date)
        ) > 1
        for history_date, status in history_rows
        if history_date
    )
    has_counts = has_multiple_checkins or has_goal_settings

    fields = list(HABIT_FILE_FIELDS)
    if has_counts:
        fields.append("count")
    if has_goal_settings:
        fields.append("goal_settings")

    details = {
        "habit_id": str(habit["_id"]),
        "name": habit.get("name", ""),
        "description": habit.get("description") or "",
        "target": habit.get("target", ""),
        "frequency": habit.get("frequency", ""),
        "created_at": created_at or "",
    }

    if has_goal_settings:
        settings = {
            "initial_target": max(
                int(habit.get("initial_target") or habit.get("target") or 1),
                1,
            ),
            "target_history": habit.get("target_history") or [],
        }
        for field in ("start_date", "goal_suggestion_last_decision"):
            if habit.get(field):
                settings[field] = habit[field]

        details["goal_settings"] = json.dumps(
            settings,
            sort_keys=True,
            separators=(",", ":"),
        )

    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    for history_date, status in history_rows:
        row = {
            **details,
            "completed_date": history_date,
            "status": status,
        }

        if has_counts:
            row["count"] = (
                completion_count_on(
                    habit, date.fromisoformat(history_date)
                )
                if history_date and status == "completed"
                else 0
            )

        writer.writerow(row)

    filename = secure_filename(habit.get("name", ""))[:80] or "habit"
    response = send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"{filename}-{habit_id}.habit",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response

def _goal_decision_query(query, habit):
    """Require eligibility-related data to match the snapshot we checked."""
    fields = (
        "frequency",
        "status",
        "start_date",
        "created_at",
        "target",
        "initial_target",
        "target_history",
        "completed_dates",
        "completion_history",
        "skipped_dates",
        "breaks",
        "goal_suggestion_last_decision",
    )
    conditions = [dict(query)]
    for field in fields:
        if field in habit:
            conditions.append({field: {"$eq": habit[field]}})
        else:
            conditions.append({field: {"$exists": False}})

    return {"$and": conditions}


@app.route(
    "/habits/<habit_id>/goal-increase/<decision>",
    methods=["POST"],
)
def decide_goal_increase(habit_id, decision):
    """Accept or dismiss a currently eligible daily-goal suggestion."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    _validate_habit_form_token()

    if decision not in ("accept", "dismiss"):
        abort(400, description="Choose accept or dismiss.")

    today = current_app_date()
    suggestion = goal_increase_suggestion(habit, today)

    if suggestion is None:
        abort(
            409,
            description="This goal suggestion is no longer available.",
        )

    if (
        request.form.get("current_target")
        != str(suggestion["current_target"])
        or request.form.get("effective_date")
        != suggestion["effective_date"]
    ):
        abort(
            409,
            description="This goal suggestion has changed. Reload the Dashboard.",
        )

    updates = {
        "goal_suggestion_last_decision": today.isoformat(),
    }

    if decision == "accept":
        history = [
            dict(change)
            for change in habit.get("target_history") or []
        ]
        history.append(
            {
                "effective_date": suggestion["effective_date"],
                "target": suggestion["suggested_target"],
            }
        )
        history.sort(key=lambda change: change["effective_date"])

        updates.update(
            {
                "initial_target": max(
                    int(
                        habit.get("initial_target")
                        or suggestion["current_target"]
                    ),
                    1,
                ),
                "target": suggestion["suggested_target"],
                "target_history": history,
            }
        )

    result = get_db()["habits"].update_one(
        _goal_decision_query(query, habit),
        {"$set": updates},
    )

    if result.matched_count == 0:
        abort(
            409,
            description="This habit changed. Reload the Dashboard and try again.",
        )

    if decision == "accept":
        flash(
            "Goal increase accepted. Your new daily target starts tomorrow.",
            "success",
        )
    else:
        flash("Goal suggestion dismissed. Your target is unchanged.", "success")

    return redirect(url_for("habit_dashboard"))

def _target_edit_updates(habit, target, frequency, today):
    """Schedule daily target edits without changing today's target."""
    old_frequency = habit.get("frequency") or "daily"
    has_dated_targets = (
        "initial_target" in habit or bool(habit.get("target_history"))
    )

    if frequency != old_frequency and has_dated_targets:
        raise ValueError(
            "Frequency cannot be changed after dated target changes. "
            "Create a new habit for a different frequency."
        )

    if frequency != "daily" or old_frequency != "daily":
        return {}

    stored_target = max(int(habit.get("target") or 1), 1)
    if target == stored_target:
        return {}

    today_target = target_on(habit, today)
    history = [
        dict(change)
        for change in habit.get("target_history") or []
        if date.fromisoformat(change["effective_date"]) <= today
    ]
    history.sort(key=lambda change: change["effective_date"])

    if target != today_target:
        history.append(
            {
                "effective_date": (today + timedelta(days=1)).isoformat(),
                "target": target,
            }
        )

    return {
        "initial_target": max(
            int(habit.get("initial_target") or stored_target), 1
        ),
        "target_history": history,
        "goal_suggestion_last_decision": today.isoformat(),
    }

@app.route("/habits/<habit_id>/edit", methods=["GET", "POST"])
def edit_habit(habit_id):
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    today = current_app_date()
    errors = []
    form = {
        "name": habit.get("name", ""),
        "description": habit.get("description") or "",
        "target": str(habit.get("target", "")),
        "frequency": habit.get("frequency", ""),
        "reminder_enabled": habit.get("reminder_enabled", False),
        "reminder_time": habit.get("reminder_time") or "",
        "reminder_timezone": habit.get("reminder_timezone") or "UTC",
        "start_date": habit.get("start_date") or "",
        "when_missed": habit.get("when_missed") or "fail",
        "reminder_weekdays": habit.get("reminder_weekdays") or [],
        "reminder_month_day": habit.get("reminder_month_day") or "",
    }

    if request.method == "POST":
        _validate_habit_form_token()
        form = {
            key: request.form.get(key, "").strip()
            for key in form
            if key not in ("reminder_weekdays",)
        }
        form["reminder_weekdays"] = request.form.getlist("reminder_weekdays")
        form["reminder_enabled"] = request.form.get("reminder_enabled") == "on"
        if form["frequency"] == habit.get("frequency"):
            if "reminder_weekdays" not in request.form:
                form["reminder_weekdays"] = list(habit.get("reminder_weekdays") or [])
            if "reminder_month_day" not in request.form:
                form["reminder_month_day"] = habit.get("reminder_month_day") or ""

        errors, target = _validate_habit_form(
            form,
            max_name_length=100,
            allow_legacy_schedule=(
                "reminder_weekdays" not in request.form
                and "reminder_month_day" not in request.form
            ),
        )
        start_date, start_date_error = _parse_start_date(
            form["start_date"], today, current=habit.get("start_date")
        )
        if start_date_error:
            errors.append(start_date_error)

        when_missed, when_missed_error = _parse_when_missed(
            form["when_missed"]
        )
        if when_missed_error:
            errors.append(when_missed_error)

        target_updates = {}
        if not errors:
            try:
                target_updates = _target_edit_updates(
                    habit,
                    target,
                    form["frequency"],
                    today,
                )
            except ValueError as error:
                errors.append(str(error))

        if not errors:
            result = get_db()["habits"].update_one(
                _goal_decision_query(query, habit),
                {
                    "$set": {
                        "name": form["name"],
                        "description": form["description"] or None,
                        "target": target,
                        "frequency": form["frequency"],
                        "reminder_enabled": form["reminder_enabled"],
                        "reminder_time": form["reminder_time"] or None,
                        "reminder_timezone": form["reminder_timezone"] or "UTC",
                        "start_date": start_date,
                        "when_missed": when_missed,
                        "reminder_weekdays": form["reminder_weekdays"],
                        "reminder_month_day": form["reminder_month_day"],
                        **target_updates,
                    }
                },
            )
            if result.matched_count == 0:
                abort(
                    409,
                    description=(
                        "This habit changed. Reload the Edit page and try again."
                    ),
                )
            return redirect(url_for("habit_dashboard"))

    return render_template(
        "apps/habit_tracker/index.html",
        page_id="habit-tracker",
        form=form,
        errors=errors,
        frequency_choices=FREQUENCY_CHOICES,
        editing=True,
        current_daily_target=(
            target_on(habit, today)
            if habit.get("frequency") == "daily"
            else None
        ),
        habit_id=habit_id,
        today=today.isoformat(),
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


@app.route("/habits/<habit_id>/skip", methods=["POST"])
def skip_habit(habit_id):
    """Mark an owned habit as skipped for today."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    _validate_habit_form_token()

    today_date = current_app_date()
    today = today_date.isoformat()
    if today in habit.get("completed_dates", []):
        flash("This habit is already completed today, so it can't be skipped.", "error")
    elif not is_due_today(habit, today_date):
        flash("This habit isn't scheduled for today.", "error")
    else:
        # completed and skipped are mutually exclusive for a given day
        get_db()["habits"].update_one(
            {**query, "completed_dates": {"$ne": today}},
            {"$addToSet": {"skipped_dates": today}},
        )
    return redirect(url_for("habit_dashboard"))


@app.route("/habits/<habit_id>/unskip", methods=["POST"])
def unskip_habit(habit_id):
    """Undo today's skip so the habit is incomplete again."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, _ = _owned_habit(habit_id)
    _validate_habit_form_token()

    today = current_app_date().isoformat()
    get_db()["habits"].update_one(query, {"$pull": {"skipped_dates": today}})
    return redirect(url_for("habit_dashboard"))


@app.route("/habits/<habit_id>/pause", methods=["GET", "POST"])
def pause_habit(habit_id):
    """Put an owned habit on a break for a chosen number of days, starting today.

    Completion and skip history are left untouched, so streak data stays frozen.
    """
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    today = current_app_date()
    if current_break(habit, today):
        flash("This habit is already on a break.", "error")
        return redirect(url_for("habit_dashboard"))

    error = None
    if request.method == "POST":
        _validate_habit_form_token()
        try:
            days = int(request.form.get("days", ""))
        except ValueError:
            days = 0

        if days not in PAUSE_DURATIONS:
            error = "Choose how long the break should last."
        else:
            end = today + timedelta(days=days - 1)
            get_db()["habits"].update_one(
                query,
                {"$push": {"breaks": {"start": today.isoformat(), "end": end.isoformat()}}},
            )
            resume = end + timedelta(days=1)
            flash(
                f"“{habit.get('name', 'This habit')}” is on a break. "
                f"It will be back on {resume:%B %d, %Y}.",
                "success",
            )
            return redirect(url_for("habit_dashboard"))

    return render_template(
        "apps/habit_tracker/pause.html",
        habit=habit,
        habit_id=habit_id,
        pause_options=[
            (days, label, today + timedelta(days=days)) for days, label in PAUSE_DURATIONS.items()
        ],
        error=error,
        habit_csrf_token=_habit_form_token(),
    ), (400 if error else 200)


@app.route("/habits/<habit_id>/resume", methods=["POST"])
def resume_habit(habit_id):
    """End an owned habit's break early so it is active again from today."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    _validate_habit_form_token()

    today = current_app_date()
    if not current_break(habit, today):
        flash("This habit isn't on a break.", "error")
        return redirect(url_for("habit_dashboard"))

    # Cut the current break off at yesterday. Past breaks stay recorded so they
    # keep counting as neutral days for progress and streaks.
    today_iso = today.isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    breaks = []
    for habit_break in habit.get("breaks") or []:
        if habit_break["end"] >= today_iso:
            if habit_break["start"] >= today_iso:
                continue  # the break only started today, so nothing is left of it
            habit_break = {**habit_break, "end": yesterday}
        breaks.append(habit_break)

    get_db()["habits"].update_one(query, {"$set": {"breaks": breaks}})
    flash(f"Welcome back! “{habit.get('name', 'This habit')}” is active again.", "success")
    return redirect(url_for("habit_dashboard"))


@app.route("/account/delete", methods=["GET", "POST"])
@app.route("/account/delete-account", methods=["GET", "POST"])
def delete_account():
    """Delete the authenticated account and every record tied to it."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    user_email = session["user_email"]
    user_id = session.get("user_id")
    query = {"user_email": user_email}

    if request.method == "POST":
        _validate_habit_form_token()
        db = get_db()
        db["habits"].delete_many(query)
        db["notifications"].delete_many(query)
        db["push_subscriptions"].delete_many(query)

        user_filter = {"email": user_email}
        if user_id and ObjectId.is_valid(user_id):
            user_filter = {"$or": [{"_id": ObjectId(user_id)}, {"email": user_email}]}
        db["users"].delete_one(user_filter)

        try:
            passcode_store().remove(user_id)
        except (OSError, sqlite3.Error):
            app.logger.error("Could not remove the local passcode for a deleted account.")

        session.clear()
        flash("Your account and all of your data have been deleted.", "success")
        return redirect(url_for("home"))

    return render_template(
        "account/delete_account.html",
        habit_count=get_db()["habits"].count_documents(query),
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


@app.route("/habits/delete-selected", methods=["POST"])
@login_required
def delete_selected_habits():
    """Delete only selected habits belonging to the signed-in user."""
    _validate_habit_form_token()

    submitted_ids = request.form.getlist("habit_ids")

    if not submitted_ids:
        flash("Select at least one habit to delete.", "error")
        return redirect(url_for("habit_dashboard"))

    if any(not ObjectId.is_valid(habit_id) for habit_id in submitted_ids):
        abort(400, description="Invalid habit selection. Reload the Dashboard and try again.")

    # Normalize and deduplicate IDs before constructing the deletion filter.
    habit_ids = list({ObjectId(habit_id) for habit_id in submitted_ids})

    result = get_db()["habits"].delete_many(
        {
            "_id": {"$in": habit_ids},
            "user_email": session["user_email"],
        }
    )

    deleted_count = result.deleted_count

    if deleted_count:
        label = "habit" if deleted_count == 1 else "habits"
        flash(f"{deleted_count} {label} deleted successfully.", "success")
    else:
        flash(
            "No habits were deleted. They may have already been removed.",
            "error",
        )

    return redirect(url_for("habit_dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
