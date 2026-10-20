import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

try:
    import certifi
except ImportError:  # pragma: no cover - certifi is a declared dependency
    certifi = None

from flask import Flask, flash, redirect, render_template, request, session, url_for
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-in-production"

# Habits live in SQLite through SQLAlchemy. The URI can be overridden through the
# environment so the test suite can point at an in-memory database instead of the
# real instance/app.db file.
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("SQLALCHEMY_DATABASE_URI", "sqlite:///app.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "connect_args": {
        "timeout": 30,
        "check_same_thread": False,
    }
}


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and busy timeout to avoid SQLite OperationalError (database locked)."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
    except sqlite3.Error as e:
        app.logger.warning(f"Could not apply SQLite pragmas: {e}")


db.init_app(app)

from models import FREQUENCY_CHOICES, Habit  # noqa: E402

# ---------------------------------------------------------------------------
# User accounts live in MongoDB Atlas (see docs/DATABASE.md). One MongoClient is
# shared per process, as pymongo recommends; creating a client per request leaks
# connection pools and monitor threads.
# ---------------------------------------------------------------------------

USERS_COLLECTION = "users"
ENV_FILES = ("atlas-credentials.env", ".env")

_mongo_lock = threading.Lock()
_mongo_client = None
_mongo_client_uri = None
_env_files_loaded = False


def _load_env_files():
    """Load MONGODB_* settings from a local env file without overriding the shell."""
    global _env_files_loaded
    if _env_files_loaded:
        return
    for filename in ENV_FILES:
        credentials_path = Path(__file__).with_name(filename)
        if not credentials_path.exists():
            continue
        for line in credentials_path.read_text().splitlines():
            key, separator, value = line.partition("=")
            key = key.strip()
            if separator and key and not key.startswith("#"):
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))
    _env_files_loaded = True


def _uses_tls(mongo_uri):
    """Atlas (mongodb+srv) and explicit tls/ssl URIs need a CA bundle; local mongod does not."""
    lowered = mongo_uri.lower()
    return lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered


def get_mongo_client(mongo_uri):
    """Return the process-wide MongoClient for the given URI, creating it on first use."""
    global _mongo_client, _mongo_client_uri
    with _mongo_lock:
        if _mongo_client is None or _mongo_client_uri != mongo_uri:
            kwargs = {"serverSelectionTimeoutMS": 5000}
            if certifi and _uses_tls(mongo_uri):
                kwargs["tlsCAFile"] = certifi.where()
            _mongo_client = MongoClient(mongo_uri, **kwargs)
            _mongo_client_uri = mongo_uri
        return _mongo_client


def get_mongo_db():
    """Return the MongoDB database that stores user accounts, or None if unconfigured."""
    if app.config.get("MONGO_DB") is not None:
        return app.config["MONGO_DB"]

    _load_env_files()
    mongo_uri = os.getenv("MONGODB_URI")
    database_name = os.getenv("MONGODB_DATABASE", "habit_tracker")
    if not mongo_uri:
        app.logger.warning("MONGODB_URI is not set; user accounts are unavailable.")
        return None

    try:
        return get_mongo_client(mongo_uri)[database_name]
    except (OSError, PyMongoError, RuntimeError) as e:
        app.logger.error(f"Could not create MongoDB client: {e}")
        return None


def ensure_user_indexes(users):
    """Create the unique indexes that keep emails and usernames unique across requests.

    create_index is idempotent, so calling this before every registration is safe.
    The username index is sparse because accounts created by the login story may
    not have a username field.
    """
    users.create_index([("email", ASCENDING)], unique=True, name="email_unique")
    users.create_index([("username", ASCENDING)], unique=True, sparse=True, name="username_unique")


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
            mongo_db = get_mongo_db()
            user = None
            if mongo_db is not None:
                try:
                    user = mongo_db[USERS_COLLECTION].find_one({"email": email})
                except (OSError, PyMongoError) as e:
                    app.logger.error(f"Error looking up user during login: {e}")
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
            try:
                habit = Habit(
                    name=form["name"],
                    description=form["description"] or None,
                    target=target,
                    frequency=form["frequency"],
                )
                db.session.add(habit)
                db.session.commit()
                return redirect(url_for("habit_dashboard"))
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Error creating habit: {e}")
                errors.append("A database error occurred. Please try again.")

    return (
        render_template(
            "apps/habit_tracker/index.html",
            page_id="habit-tracker",
            form=form,
            errors=errors,
            frequency_choices=FREQUENCY_CHOICES,
        ),
        (400 if errors else 200),
    )


@app.route("/dashboard")
def habit_dashboard():
    try:
        habits = Habit.query.order_by(Habit.created_at.desc()).all()
    except Exception as e:
        app.logger.error(f"Error loading dashboard habits: {e}")
        habits = []

    today = datetime.utcnow().date().isoformat()

    completion_by_id = {
        habit.id: today
        in {
            date_value.strip()
            for date_value in (habit.completed_dates or "").split(",")
            if date_value.strip()
        }
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
    if session.get("user_id"):
        return redirect(url_for("habit_dashboard"))

    username = ""
    email = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Input validation
        error = None
        if not username or not email or not password:
            error = "All fields are required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters long."
        elif password != confirm_password:
            error = "Passwords do not match."

        if error is None:
            mongo_db = get_mongo_db()
            if mongo_db is None:
                error = "Database connection error. Please try again later."
            else:
                error = _create_user(mongo_db[USERS_COLLECTION], username, email, password)

        if error is None:
            flash("Registration successful! Welcome to Habit Tracker.", "success")
            return redirect(url_for("habit_dashboard"))

        flash(error, "error")

    return render_template("auth/register.html", page_id="register", username=username, email=email)


def _create_user(users, username, email, password):
    """Insert a new account and log it in. Returns an error message, or None on success.

    The document shape matches what the login route reads: the werkzeug hash is
    stored once, under "password".
    """
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


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


def init_db():
    with app.app_context():
        try:
            db.create_all()
            _migrate_habit_table()
        except Exception as e:
            app.logger.warning(f"Database initialization warning: {e}")


def _migrate_habit_table():
    """Add habit columns introduced after an existing database was created."""
    try:
        inspector = inspect(db.engine)
        if not inspector.has_table("habit"):
            return

        columns = {column["name"] for column in inspector.get_columns("habit")}
        missing_columns = {
            "target": "INTEGER",
            "frequency": "VARCHAR(20)",
            "completed_dates": "TEXT",
            "created_at": "DATETIME",
            "description": "TEXT",
        }

        with db.engine.begin() as connection:
            for name, column_type in missing_columns.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE habit ADD COLUMN {name} {column_type}"))
    except Exception as e:
        app.logger.warning(f"Habit table migration notice: {e}")


init_db()


if __name__ == "__main__":
    app.run(debug=True)
