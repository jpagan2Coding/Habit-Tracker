from app import app


def test_logout():
    app.config["TESTING"] = True

    with app.test_client() as client:
        # Simulate a logged-in user
        with client.session_transaction() as session:
            session["user_id"] = 1

        # Attempt to log out
        response = client.get("/logout")

        # Check redirect to login page
        assert response.status_code == 302
        assert "/login" in response.location

        # Check that the user session is cleared
        with client.session_transaction() as session:
            assert "user_id" not in session
