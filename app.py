import os
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash

from models import FREQUENCY_CHOICES

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-in-production"
def get_db():
    """Connect to the team's MongoDB database using MONGODB_URI."""
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        raise RuntimeError("MONGODB_URI is not configured")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    return client[os.getenv("MONGODB_DATABASE", "habit_tracker")]


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
                mongo_uri = os.getenv("MONGODB_URI")
                database_name = os.getenv("MONGODB_DATABASE", "habit_tracker")
                if not mongo_uri:
                    raise RuntimeError("MONGODB_URI is not configured")

                client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
                database = client[database_name]
                user = database["users"].find_one({"email": email})
            except (OSError, PyMongoError, RuntimeError):
                user = None

            stored_password = user.get("password", "") if user else ""
            if user and check_password_hash(stored_password, password):
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


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))  # change to login once implemented


if __name__ == "__main__":
    app.run(debug=True)
