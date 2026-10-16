
from app import app


def test_logout():
    app.config["TESTING"] = True

    with app.test_client() as client:
        # Simulate a logged-in user
        with client.session_transaction() as session:
            session["user_id"] = 1

        # Log out
        response = client.post("/logout")

        # Check redirect to home page
        assert response.status_code == 302
        assert response.location.endswith("/")

        # Check that the user session was cleared
        with client.session_transaction() as session:
            assert "user_id" not in session


def test_logout_without_login():
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post("/logout")

        # Logging out without a session should not crash
        assert response.status_code == 302

