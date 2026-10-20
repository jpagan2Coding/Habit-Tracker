import os
from datetime import datetime
from pathlib import Path

try:
    import certifi
except ImportError:
    certifi = None

from flask import Flask, flash, redirect, render_template, request, session, url_for
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-in-production"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

from models import FREQUENCY_CHOICES, Habit  # noqa: E402


def get_mongo_db():
    """Connect to MongoDB using test config, atlas-credentials.env, or environment variables."""
    if app.config.get("MONGO_DB") is not None:
        return app.config["MONGO_DB"]

    for filename in ("atlas-credentials.env", ".env"):
        credentials_path = Path(__file__).with_name(filename)
        if credentials_path.exists():
            for line in credentials_path.read_text().splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() and not key.lstrip().startswith("#"):
                    os.environ.setdefault(
                        key.strip(), value.strip().strip('"').strip("'")
                    )

    mongo_uri = os.getenv("MONGODB_URI")
    database_name = os.getenv("MONGODB_DATABASE", "habit_tracker")
    if not mongo_uri:
        return None

    try:
        kwargs = {"serverSelectionTimeoutMS": 5000}
        if certifi:
            kwargs["tlsCAFile"] = certifi.where()
        client = MongoClient(mongo_uri, **kwargs)
        return client[database_name]
    except (OSError, PyMongoError, RuntimeError):
        return None


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
                    user = mongo_db["users"].find_one({"email": email})
                except (OSError, PyMongoError):
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
            habit = Habit(
                name=form["name"],
                description=form["description"] or None,
                target=target,
                frequency=form["frequency"],
            )
            db.session.add(habit)
            db.session.commit()
            return redirect(url_for("habit_dashboard"))

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
    habits = Habit.query.order_by(Habit.created_at.desc()).all()

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

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Input Validation
        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("auth/register.html", username=username, email=email)

        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return render_template("auth/register.html", username=username, email=email)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("auth/register.html", username=username, email=email)

        mongo_db = get_mongo_db()
        if mongo_db is None:
            flash("Database connection error. Please try again later.", "error")
            return render_template("auth/register.html", username=username, email=email)

        users_col = mongo_db["users"]

        # Check existing username / email
        try:
            if users_col.find_one({"username": username}):
                flash("Username is already taken. Please choose another.", "error")
                return render_template("auth/register.html", username=username, email=email)

            if users_col.find_one({"email": email}):
                flash("Email is already registered. Please log in.", "error")
                return render_template("auth/register.html", username=username, email=email)

            hashed_pw = generate_password_hash(password)
            new_user = {
                "username": username,
                "email": email,
                "password": hashed_pw,
                "password_hash": hashed_pw,
                "created_at": datetime.utcnow(),
            }
            result = users_col.insert_one(new_user)
        except (OSError, PyMongoError):
            flash("Database error occurred while creating account.", "error")
            return render_template("auth/register.html", username=username, email=email)

        # Log user in after successful registration
        session["user_id"] = str(result.inserted_id)
        session["username"] = username
        session["user_email"] = email

        flash("Registration successful! Welcome to Habit Tracker.", "success")
        return redirect(url_for("habit_dashboard"))

    return render_template("auth/register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))  # change to login once implemented


def init_db():
    with app.app_context():
        db.create_all()
        _migrate_habit_table()


def _migrate_habit_table():
    """Add habit columns introduced after an existing database was created."""
    columns = {
        column['name']
        for column in inspect(db.engine).get_columns('habit')
    }
    missing_columns = {
        'target': 'INTEGER',
        'frequency': 'VARCHAR(20)',
    }

    with db.engine.begin() as connection:
        for name, column_type in missing_columns.items():
            if name not in columns:
                connection.execute(
                    text(f'ALTER TABLE habit ADD COLUMN {name} {column_type}')
                )


init_db()


if __name__ == "__main__":
    init_db()

    app.run(debug=True)
