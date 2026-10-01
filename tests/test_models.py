import pytest
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Expense, Habit, MoodEntry, Recipe


# === Habit Model Tests ===


def test_habit_create_and_persist(app):
    """Test that Habit model can be created and persisted to the database."""
    # Arrange
    habit = Habit(name="Exercise", description="Morning routine")

    # Act
    db.session.add(habit)
    db.session.commit()

    # Assert
    stored = Habit.query.first()
    assert stored is not None
    assert stored.name == "Exercise"
    assert stored.description == "Morning routine"


def test_habit_allows_optional_description(app):
    """Test that Habit model allows description to be None."""
    # Arrange
    habit = Habit(name="Meditate", description=None)

    # Act
    db.session.add(habit)
    db.session.commit()

    # Assert
    stored = Habit.query.first()
    assert stored is not None
    assert stored.description is None


# === MoodEntry Model Tests ===


def test_mood_entry_create_and_persist(app):
    """Test that MoodEntry model can be created and persisted to the database."""
    # Arrange
    entry = MoodEntry(mood="Happy", notes="Great day")

    # Act
    db.session.add(entry)
    db.session.commit()

    # Assert
    stored = MoodEntry.query.first()
    assert stored is not None
    assert stored.mood == "Happy"
    assert stored.notes == "Great day"


def test_mood_entry_requires_mood(app):
    """Test that MoodEntry model raises IntegrityError when mood is None."""
    # Arrange
    entry = MoodEntry(mood=None, notes="Forgot")
    db.session.add(entry)

    # Act & Assert
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


# === Expense Model Tests ===


def test_expense_create_and_persist(app):
    """Test that Expense model can be created and persisted to the database."""
    # Arrange
    expense = Expense(description="Lunch", amount=15.50, payer="Sam", participants="Sam, Alex")

    # Act
    db.session.add(expense)
    db.session.commit()

    # Assert
    stored = Expense.query.first()
    assert stored is not None
    assert stored.description == "Lunch"
    assert stored.amount == 15.50
    assert stored.payer == "Sam"


def test_expense_requires_amount(app):
    """Test that Expense model raises IntegrityError when amount is None."""
    # Arrange
    expense = Expense(description="Dinner", amount=None, payer="Alex")
    db.session.add(expense)

    # Act & Assert
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


def test_expense_requires_description(app):
    """Test that Expense model raises IntegrityError when description is None."""
    # Arrange
    expense = Expense(description=None, amount=20.00, payer="Jordan")
    db.session.add(expense)

    # Act & Assert
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


# === Recipe Model Tests ===


def test_recipe_create_and_persist(app):
    """Test that Recipe model can be created and persisted to the database."""
    # Arrange
    recipe = Recipe(
        name="Pasta",
        ingredients="Noodles, Sauce",
        instructions="Boil and mix",
        prep_time=15
    )

    # Act
    db.session.add(recipe)
    db.session.commit()

    # Assert
    stored = Recipe.query.first()
    assert stored is not None
    assert stored.name == "Pasta"
    assert stored.prep_time == 15


def test_recipe_allows_optional_prep_time(app):
    """Test that Recipe model allows prep_time to be None."""
    # Arrange
    recipe = Recipe(
        name="Salad",
        ingredients="Lettuce, Tomato",
        instructions="Mix together",
        prep_time=None,
    )

    # Act
    db.session.add(recipe)
    db.session.commit()

    # Assert
    stored = Recipe.query.first()
    assert stored is not None
    assert stored.prep_time is None
