from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for

from extensions import db

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-in-production"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

from models import FREQUENCY_CHOICES, Habit, User  # noqa: E402


@app.route("/")
def home():
    return render_template("home/index.html", page_id="home")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Render the login screen and provide demo access to the tracker."""
    error = None
    email = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Enter both your email and password to continue."
        else:
            # TODO: Implement proper auth here
            session["user_email"] = email
            return redirect(url_for("habit_dashboard"))

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

    completed_today = sum(
        1
        for habit in habits
        if habit.completed_dates
        and today in [date_value.strip() for date_value in habit.completed_dates.split(",")]
    )

    total_habits = len(habits)
    scheduled_today = total_habits

    completion_percentage = (
        round((completed_today / scheduled_today) * 100) if scheduled_today else 0
    )

    return render_template(
        "apps/habit_tracker/dashboard.html",
        page_id="dashboard",
        habits=habits,
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

        # Check existing username / email
        if User.query.filter_by(username=username).first():
            flash("Username is already taken. Please choose another.", "error")
            return render_template("auth/register.html", username=username, email=email)

        if User.query.filter_by(email=email).first():
            flash("Email is already registered. Please log in.", "error")
            return render_template("auth/register.html", username=username, email=email)

        # Create new user
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # Log user in after successful reg
        session["user_id"] = new_user.id
        session["username"] = new_user.username

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


init_db()


if __name__ == "__main__":
    init_db()

    app.run(debug=True)
