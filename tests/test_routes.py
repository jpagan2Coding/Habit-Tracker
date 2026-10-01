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


# === Mood Journal Tests ===


def test_mood_journal_get_returns_ok(client):
    """Test that GET /mood-journal returns a 200 status code."""
    # Act
    response = client.get('/mood-journal')

    # Assert
    assert response.status_code == 200


def test_mood_journal_post_creates_entry(client):
    """Test that POST /mood-journal creates a new mood entry in the database."""
    # Arrange
    entry_data = {'mood': 'Joyful', 'notes': 'Sunshine and coffee'}

    # Act
    response = client.post('/mood-journal', data=entry_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    stored = MoodEntry.query.filter_by(mood='Joyful').first()
    assert stored is not None
    assert stored.notes == 'Sunshine and coffee'


def test_mood_journal_rejects_blank_mood(client):
    """Test that POST /mood-journal rejects entries with blank mood values."""
    # Arrange
    invalid_data = {'mood': '  ', 'notes': 'Forgot to fill mood'}

    # Act
    response = client.post('/mood-journal', data=invalid_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    assert MoodEntry.query.count() == 0


# === Expense Splitter Tests ===


def test_expense_splitter_get_returns_ok(client):
    """Test that GET /expense-splitter returns a 200 status code."""
    # Act
    response = client.get('/expense-splitter')

    # Assert
    assert response.status_code == 200


def test_expense_splitter_post_creates_expense(client):
    """Test that POST /expense-splitter creates a new expense in the database."""
    # Arrange
    expense_data = {
        'description': 'Dinner',
        'amount': '36.75',
        'payer': 'Alex',
        'participants': 'Alex, Sam, Jo',
    }

    # Act
    response = client.post('/expense-splitter', data=expense_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    stored = Expense.query.filter_by(description='Dinner').first()
    assert stored is not None
    assert stored.amount == 36.75
    assert stored.payer == 'Alex'
    assert stored.participants == 'Alex, Sam, Jo'


def test_expense_splitter_rejects_invalid_amount(client):
    """Test that POST /expense-splitter rejects non-numeric amount values."""
    # Arrange
    invalid_data = {
        'description': 'Snacks',
        'amount': 'abc',
        'payer': 'Riley',
        'participants': 'Riley, Pat',
    }

    # Act
    response = client.post('/expense-splitter', data=invalid_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    assert Expense.query.count() == 0


# === Recipe Assistant Tests ===


def test_recipe_assistant_get_returns_ok(client):
    """Test that GET /recipe-assistant returns a 200 status code."""
    # Act
    response = client.get('/recipe-assistant')

    # Assert
    assert response.status_code == 200


def test_recipe_assistant_post_creates_recipe(client):
    """Test that POST /recipe-assistant creates a new recipe in the database."""
    # Arrange
    recipe_data = {
        'name': 'Pancakes',
        'ingredients': 'Flour, Eggs, Milk',
        'instructions': 'Mix and fry',
        'prep_time': '25',
    }

    # Act
    response = client.post('/recipe-assistant', data=recipe_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    stored = Recipe.query.filter_by(name='Pancakes').first()
    assert stored is not None
    assert stored.prep_time == 25
    assert stored.ingredients == 'Flour, Eggs, Milk'


def test_recipe_assistant_rejects_missing_ingredients(client):
    """Test that POST /recipe-assistant rejects recipes with missing required fields."""
    # Arrange
    invalid_data = {
        'name': 'Smoothie',
        'ingredients': '',
        'instructions': 'Blend everything',
        'prep_time': '5',
    }

    # Act
    response = client.post('/recipe-assistant', data=invalid_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    assert Recipe.query.count() == 0


def test_recipe_assistant_stores_negative_prep_time_as_none(client):
    """Test that POST /recipe-assistant handles negative prep_time by storing None."""
    # Arrange
    recipe_data = {
        'name': 'Soup',
        'ingredients': 'Water, Veggies',
        'instructions': 'Simmer gently',
        'prep_time': '-10',
    }

    # Act
    response = client.post('/recipe-assistant', data=recipe_data, follow_redirects=False)

    # Assert
    assert response.status_code == 302
    stored = Recipe.query.filter_by(name='Soup').first()
    assert stored is not None
    assert stored.prep_time is None


# === Parametrized Tests ===


@pytest.mark.parametrize(
    "endpoint",
    ["/habit-tracker", "/mood-journal", "/expense-splitter", "/recipe-assistant"]
)
def test_all_modules_get_returns_ok(client, endpoint):
    """Test that all module endpoints return 200 status code on GET requests."""
    # Act
    response = client.get(endpoint)

    # Assert
    assert response.status_code == 200
