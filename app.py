import csv
import io
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache, wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

from models import FREQUENCY_CHOICES, HABIT_FILE_FIELDS, PAUSE_DURATIONS
from progress import (
    VALID_PROGRESS_PERIODS,
    calculate_overall_progress,
    current_break,
    habit_summary,
    period_start,
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


load_dotenv(Path(__file__).resolve().with_name("atlas-credentials.env"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")


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
                session.clear()
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
@login_required
def habit_tracker():
    today = datetime.utcnow().date()
    form = {
        "name": "",
        "description": "",
        "target": "",
        "frequency": "",
        "reminder_enabled": False,
        "reminder_time": "",
        "reminder_timezone": "UTC",
        "start_date": today.isoformat(),
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
    """Check if a habit should show up on today's list."""
    if habit.get("status", "active") in INACTIVE_STATUSES:
        return False

    today_iso = today.isoformat()

    # habits that haven't reached their start date yet aren't due (US-23)
    start_date = habit.get("start_date")
    if start_date and start_date > today_iso:
        return False
    
    completed = {d for d in habit.get("completed_dates", []) if isinstance(d, str)}
    skipped = {d for d in habit.get("skipped_dates", []) if isinstance(d, str)}

    # a habit on a break leaves the list, unless it was already finished before the break
    if current_break(habit, today):
        return today_iso in completed

    # keep habits finished or skipped today on the list so they show their status
    if today_iso in completed or today_iso in skipped:
        return True

    # due until the target is hit for this period (skipped days don't count toward it)
    start_iso = period_start(habit.get("frequency") or "daily", today).isoformat()
    completed_this_period = sum(1 for d in completed if start_iso <= d <= today_iso)
    return completed_this_period < (habit.get("target") or 1)


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

    today_date = datetime.utcnow().date()
    today = today_date.isoformat()
    progress_period = _selected_progress_period()

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

    completion_by_id = {habit["id"]: today in habit.get("completed_dates", []) for habit in habits}

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
        completion_by_id=completion_by_id,
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
        # Not built yet. Team rule: a skipped day neither breaks nor extends a streak, and
        # neither does a day on a break (use progress.current_break to spot those).
        current_streak=None,
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
    today = datetime.utcnow().date()
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
def settings():
    if request.method == "POST":
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

    return render_template(
        "settings.html",
        page_id="settings",
        text_sizes=TEXT_SIZES,
    )


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


@app.route("/habits/<habit_id>/complete", methods=["POST"])
def complete_habit(habit_id):
    """Record one daily completion for the authenticated user's habit."""

    if not session.get("user_id") or not session.get("user_email"):
        return jsonify({"error": "Login required"}), 401

    _validate_habit_form_token()

    query, habit = _owned_habit(habit_id)
    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()

    if habit.get("status", "active") in INACTIVE_STATUSES:
        return jsonify({"error": "Habit is inactive"}), 409

    if current_break(habit, today):
        return jsonify({"error": "Habit is on a break"}), 409

    if today_str in habit.get("completed_dates", []):
        return jsonify({"error": "Already completed today"}), 409

    if not is_due_today(habit, today):
        return jsonify({"error": "Habit target already reached"}), 409

    # completing a habit skipped today replaces the skip (a day can't be both)
    result = get_db()["habits"].update_one(
        {
            **query,
            "completed_dates": {"$ne": today_str},
        },
        {
            "$addToSet": {"completed_dates": today_str},
            "$pull": {"skipped_dates": today_str},
        },
    )

    if result.modified_count == 0:
        return jsonify({"error": "Completion not recorded"}), 409

    return jsonify(
        {
            "habit_id": habit_id,
            "completed": True,
            "completed_date": today_str,
            "message": "Habit marked as complete",
        }
    ), 200


@app.route("/habits/<habit_id>/uncomplete", methods=["POST"])
def uncomplete_habit(habit_id):
    """Undo today's completion for the authenticated user's habit."""

    if not session.get("user_id") or not session.get("user_email"):
        return jsonify({"error": "Login required"}), 401

    _validate_habit_form_token()

    query, habit = _owned_habit(habit_id)
    today_str = datetime.now(timezone.utc).date().isoformat()

    if today_str not in habit.get("completed_dates", []):
        return jsonify({"error": "Habit is not completed today"}), 409

    result = get_db()["habits"].update_one(
        {
            **query,
            "completed_dates": today_str,
        },
        {
            "$pull": {"completed_dates": today_str},
        },
    )

    if result.modified_count == 0:
        return jsonify({"error": "Completion not removed"}), 409

    return jsonify(
        {
            "habit_id": habit_id,
            "completed": False,
            "completed_date": today_str,
            "message": "Habit completion undone",
        }
    ), 200

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


def _parse_habit_file(payload):
    """Validate a single exported habit completely before any database write."""
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    if reader.fieldnames != HABIT_FILE_FIELDS:
        raise ValueError("The file must contain the exported habit columns in order.")

    details = None
    completed_dates = []
    skipped_dates = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Every row must contain all habit columns.")
        current = {
            field: row[field]
            for field in HABIT_FILE_FIELDS
            if field not in ("completed_date", "status")
        }
        if details is not None and current != details:
            raise ValueError("All rows must describe the same habit.")
        details = current
        history = _parse_history_row(row)
        if history is not None:
            history_date, status = history
            dates = completed_dates if status == "completed" else skipped_dates
            if history_date not in dates:
                dates.append(history_date)

    if details is None:
        raise ValueError("The file contains no habit.")
    if not details["name"].strip():
        raise ValueError("Enter a habit name.")
    if not details["target"].isdigit() or int(details["target"]) < 1:
        raise ValueError("Target must be a positive whole number.")
    if details["frequency"] not in FREQUENCY_CHOICES:
        raise ValueError("Choose a supported frequency.")
    created_at = details["created_at"]
    if created_at:
        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at = created_at.astimezone(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)
    return {
        "name": details["name"],
        "description": details["description"],
        "target": int(details["target"]),
        "frequency": details["frequency"],
        "created_at": created_at,
        "completed_dates": completed_dates,
        "skipped_dates": skipped_dates,
    }


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
    """Download an owned habit and its completion and skip history as UTF-8 CSV."""
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    _, habit = _owned_habit(habit_id)
    created_at = _format_export_datetime(habit.get("created_at"))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HABIT_FILE_FIELDS)
    writer.writeheader()
    details = {
        "habit_id": str(habit["_id"]),
        "name": habit.get("name", ""),
        "description": habit.get("description") or "",
        "target": habit.get("target", ""),
        "frequency": habit.get("frequency", ""),
        "created_at": created_at or "",
    }
    for history_date, status in _serialize_habit_history(habit):
        writer.writerow(
            {
                **details,
                "completed_date": history_date,
                "status": status,
            }
        )

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


@app.route("/habits/<habit_id>/edit", methods=["GET", "POST"])
def edit_habit(habit_id):
    if not session.get("user_id") or not session.get("user_email"):
        return redirect(url_for("login"))

    query, habit = _owned_habit(habit_id)
    today = datetime.utcnow().date()
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
                        "start_date": start_date,
                        "reminder_weekdays": form["reminder_weekdays"],
                        "reminder_month_day": form["reminder_month_day"],
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

    today_date = datetime.utcnow().date()
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

    today = datetime.utcnow().date().isoformat()
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
    today = datetime.utcnow().date()
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

    today = datetime.utcnow().date()
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
