from datetime import datetime

from extensions import db
from models import Habit


def test_habit_create_and_persist(app):
    """A habit can be saved and retrieved from the database."""
    habit = Habit(name="Exercise", description="Morning routine")

    db.session.add(habit)
    db.session.commit()

    stored = Habit.query.first()

    assert stored is not None
    assert stored.name == "Exercise"
    assert stored.description == "Morning routine"


def test_habit_allows_optional_description(app):
    """A habit can be created without a description."""
    habit = Habit(name="Meditate", description=None)

    db.session.add(habit)
    db.session.commit()

    stored = Habit.query.first()

    assert stored is not None
    assert stored.description is None


def test_habit_has_created_at_timestamp(app):
    """A saved habit receives a creation timestamp."""
    habit = Habit(name="Read", description="Read 20 pages")

    db.session.add(habit)
    db.session.commit()

    stored = Habit.query.first()

    assert stored is not None
    assert isinstance(stored.created_at, datetime)