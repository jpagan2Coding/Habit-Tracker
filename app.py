import os
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash

from extensions import db


app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

from models import FREQUENCY_CHOICES, Habit  # noqa: E402


@app.route('/')
def home():
    return render_template('home/index.html', page_id='home')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Render the login screen and authenticate an existing user."""
    error = None
    email = ''

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            error = 'Enter both your email and password to continue.'
        else:
            try:
                credentials_path = Path(__file__).with_name('atlas-credentials.env')
                if credentials_path.exists():
                    for line in credentials_path.read_text().splitlines():
                        key, separator, value = line.partition('=')
                        if separator and key.strip() and not key.lstrip().startswith('#'):
                            os.environ.setdefault(
                                key.strip(), value.strip().strip('"').strip("'")
                            )

                mongo_uri = os.getenv('MONGODB_URI')
                database_name = os.getenv('MONGODB_DATABASE', 'habit_tracker')
                if not mongo_uri:
                    raise RuntimeError('MONGODB_URI is not configured')

                client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
                database = client[database_name]
                user = database['users'].find_one({'email': email})
            except (OSError, PyMongoError, RuntimeError):
                user = None

            stored_password = user.get('password', '') if user else ''
            if user and check_password_hash(stored_password, password):
                session['user_email'] = email
                return redirect(url_for('habit_dashboard'))

            error = 'Invalid email or password.'

    return render_template(
        'auth/login.html',
        page_id='login',
        error=error,
        email=email,
    )


@app.route('/habit-tracker', methods=['GET', 'POST'])
def habit_tracker():
    form = {'name': '', 'description': '', 'target': '', 'frequency': ''}
    errors = []

    if request.method == 'POST':
        form = {key: request.form.get(key, '').strip() for key in form}

        if not form['name']:
            errors.append('Enter a name for your habit.')

        target = None
        if not form['target']:
            errors.append('Enter a target.')
        elif not form['target'].isdigit() or int(form['target']) < 1:
            errors.append('Target must be a whole number of 1 or more.')
        else:
            target = int(form['target'])

        if form['frequency'] not in FREQUENCY_CHOICES:
            errors.append('Choose a frequency.')

        if not errors:
            habit = Habit(
                name=form['name'],
                description=form['description'] or None,
                target=target,
                frequency=form['frequency'],
            )
            db.session.add(habit)
            db.session.commit()
            return redirect(url_for('habit_dashboard'))

    return render_template(
        'apps/habit_tracker/index.html',
        page_id='habit-tracker',
        form=form,
        errors=errors,
        frequency_choices=FREQUENCY_CHOICES,
    ), (400 if errors else 200)

@app.route('/dashboard')
def habit_dashboard():
    habits = Habit.query.order_by(Habit.created_at.desc()).all()

    today = datetime.utcnow().date().isoformat()

    completion_by_id = {
        habit.id: today in {
            date_value.strip()
            for date_value in (habit.completed_dates or '').split(',')
            if date_value.strip()
        }
        for habit in habits
    }

    completed_today = sum(completion_by_id.values())

    total_habits = len(habits)
    scheduled_today = total_habits

    completion_percentage = (
        round((completed_today / scheduled_today) * 100)
        if scheduled_today
        else 0
    )

    return render_template(
        'apps/habit_tracker/dashboard.html',
        page_id='dashboard',
        habits=habits,
        completion_by_id=completion_by_id,
        total_habits=total_habits,
        completed_today=completed_today,
        scheduled_today=scheduled_today,
        completion_percentage=completion_percentage,
        current_streak=None,
        today=today,
    )

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('home')) # change to login once implemented

def init_db():
    with app.app_context():
        db.create_all()


init_db()


if __name__ == '__main__':
    init_db()

    app.run(debug=True)
