"""
Pytest configuration and fixtures for testing.

This module provides shared fixtures used across all test files:
- app: Flask application instance with test configuration
- client: Flask test client for making HTTP requests
"""

import mongomock
import pytest
from werkzeug.security import generate_password_hash

from app import app as flask_app


@pytest.fixture
def mongo_client(monkeypatch):
    """
    Provide an in-memory MongoDB replacement for authentication tests.
    """
    client = mongomock.MongoClient()
    database = client["habit_tracker_test"]
    database["users"].insert_one(
        {
            "email": "person@example.com",
            "password": generate_password_hash("password"),
        }
    )

    monkeypatch.setattr("app.MongoClient", lambda *args, **kwargs: client)
    monkeypatch.setenv("MONGODB_URI", "mongodb://mocked-ci-only")
    monkeypatch.setenv("MONGODB_DATABASE", "habit_tracker_test")
    return client


@pytest.fixture
def app(mongo_client):
    """
    Flask app configured for testing, backed by the in-memory MongoDB.
    """
    flask_app.config.update(TESTING=True)
    yield flask_app


@pytest.fixture
def habits(mongo_client):
    """
    The habits collection in the in-memory test database.
    """
    return mongo_client["habit_tracker_test"]["habits"]

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
