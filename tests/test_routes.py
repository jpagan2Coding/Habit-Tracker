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


# === Parametrized Tests ===

@pytest.mark.parametrize(
    'endpoint',
    ['/habit-tracker', '/dashboard'],
)
def test_all_pages_get_returns_ok(client, endpoint):
    """Test that Habit Tracker pages return 200 status code on GET requests."""
    response = client.get(endpoint)

    assert response.status_code == 200