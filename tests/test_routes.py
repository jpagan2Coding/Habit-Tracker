import pytest
from models import Expense, Habit, MoodEntry, Recipe


# === Habit Tracker Tests ===


def test_habit_tracker_get_returns_ok(client):
    """Test that GET /habit-tracker returns a 200 status code."""
    # Act
    response = client.get('/habit-tracker')

    # Assert
    assert response.status_code == 200


def test_habit_tracker_post_creates_habit(client):
    """Test that POST /habit-tracker creates a new habit in the database."""
    # Arrange
    habit_data = {'name': 'Read 20 pages', 'description': 'Daily reading goal'}

    # Act
    response = client.post('/habit-tracker', data=habit_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    stored = Habit.query.filter_by(name='Read 20 pages').first()
    assert stored is not None
    assert stored.description == 'Daily reading goal'


# === Parametrized Tests ===


@pytest.mark.parametrize(
    "endpoint",
    ["/habit-tracker"]
)
def test_all_modules_get_returns_ok(client, endpoint):
    """Test that all module endpoints return 200 status code on GET requests."""
    # Act
    response = client.get(endpoint)

    # Assert
    assert response.status_code == 200
