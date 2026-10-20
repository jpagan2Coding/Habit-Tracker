import pytest

from models import Habit


# === Habit Tracker Tests ===

def test_habit_tracker_get_returns_ok(client):
    """Test that GET /habit-tracker returns a 200 status code."""
    response = client.get('/habit-tracker')

    assert response.status_code == 200


def test_habit_tracker_post_creates_habit(client):
    """Test that POST /habit-tracker creates a new habit in the database."""
    habit_data = {
        'name': 'Read 20 pages',
        'description': 'Daily reading goal',
        'target': '1',
        'frequency': 'daily',
    }

    response = client.post(
        '/habit-tracker',
        data=habit_data,
        follow_redirects=False,
    )

    assert response.status_code == 302

    stored = Habit.query.filter_by(name='Read 20 pages').first()

    assert stored is not None
    assert stored.description == 'Daily reading goal'


# === Dashboard Tests ===


def test_dashboard_get_returns_ok(client):
    """Test that GET /dashboard returns a 200 status code."""
    response = client.get('/dashboard')

    assert response.status_code == 200
    assert b'Dashboard' in response.data
    assert b"Today's Habits" in response.data
    assert b'Total Habits' in response.data
    assert b'All My Habits' in response.data


def test_dashboard_displays_date_and_welcome(client):
    """Dashboard displays the supplied date and welcome message."""
    from flask import template_rendered

    captured_contexts = []

    def record_template(sender, template, context, **extra):
        if template.name == 'apps/habit_tracker/dashboard.html':
            captured_contexts.append(context)

    with template_rendered.connected_to(
        record_template,
        client.application,
    ):
        response = client.get('/dashboard')

    assert response.status_code == 200
    assert len(captured_contexts) == 1

    today = captured_contexts[0]['today']

    assert today
    assert f'Today: {today}'.encode() in response.data
    assert b'Welcome back!' in response.data

# === Login Tests ===

def test_login_get_returns_ok(client):
    """Test that the login page renders successfully."""
    response = client.get('/login')

    assert response.status_code == 200
    assert b'Log in to HabitBuddy' in response.data
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
    'endpoint',
    ['/habit-tracker', '/dashboard'],
)
def test_all_pages_get_returns_ok(client, endpoint):
    """Test that Habit Tracker pages return 200 status code on GET requests."""
    response = client.get(endpoint)

    assert response.status_code == 200
