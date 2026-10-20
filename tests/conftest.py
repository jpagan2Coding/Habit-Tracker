"""
Pytest configuration and fixtures for testing.

This module provides shared fixtures used across all test files:
- mongo_client: in-memory mongomock replacement for the MongoDB "users" collection
- app: Flask application instance with test configuration
- client: Flask test client for making HTTP requests

Habits use an in-memory SQLite database. Flask-SQLAlchemy builds its engine when
app.py is imported, so the URI has to be set in the environment *before* that
import; changing app.config afterwards has no effect and would silently run the
tests against the real instance/app.db file.
"""

import os

os.environ["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"

import mongomock  # noqa: E402
import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import app as flask_app  # noqa: E402
from extensions import db  # noqa: E402


@pytest.fixture
def mongo_client(monkeypatch):
    """Provide an in-memory MongoDB replacement for authentication tests."""
    client = mongomock.MongoClient()
    database = client["habit_tracker_test"]
    database["users"].insert_one(
        {
            "email": "person@example.com",
            "password": generate_password_hash("password"),
        }
    )

    # app.get_mongo_db() returns this database directly, so no real MongoClient is
    # ever constructed and the process-wide client cache is never touched.
    monkeypatch.setitem(flask_app.config, "MONGO_DB", database)
    monkeypatch.setenv("MONGODB_URI", "mongodb://mocked-ci-only")
    monkeypatch.setenv("MONGODB_DATABASE", "habit_tracker_test")
    return client


@pytest.fixture
def app(mongo_client):
    """
    Create and configure a Flask application instance for testing.

    Uses an in-memory SQLite database whose tables are created fresh for each
    test and dropped after the test completes.

    Yields:
        Flask application configured for testing
    """
    flask_app.config.update(TESTING=True)

    with flask_app.app_context():
        # Guard against ever running the suite against a real database file.
        assert db.engine.url.database in (None, "", ":memory:"), db.engine.url

        db.session.remove()
        db.drop_all()
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """
    Create a Flask test client for making HTTP requests in tests.

    Args:
        app: Flask application fixture

    Returns:
        Flask test client instance
    """
    return app.test_client()
