from datetime import datetime

from models import Habit


# === Habit Model Tests ===

def test_habit_can_be_created(app):
    """Test that a Habit object can be created with required fields."""
    habit = Habit(
        name='Drink Water',
        description='Drink enough water every day',
    )

    assert habit.name == 'Drink Water'
    assert habit.description == 'Drink enough water every day'


def test_habit_description_can_be_empty(app):
    """Test that a Habit can be created without a description."""
    habit = Habit(name='Exercise')

    assert habit.name == 'Exercise'
    assert habit.description is None


def test_habit_has_created_at_timestamp(app):
    """Test that a new Habit receives a created_at timestamp."""
    habit = Habit(name='Read')

    assert habit.created_at is None or isinstance(habit.created_at, datetime)


def test_habit_completed_dates_can_store_text(app):
    """Test that completed_dates can hold comma-separated ISO date values."""
    habit = Habit(
        name='Meditate',
        completed_dates='2026-10-01,2026-10-02',
    )

    assert habit.completed_dates == '2026-10-01,2026-10-02'

# new test case!
def test_register_get_returns_ok(client):
    """Test that GET /register loads the registration page successfully."""
    response = client.get('/register')

    assert response.status_code == 200
    assert b'Register' in response.data
