from models import Habit


def test_habit_tracker_get_returns_ok(client):
    """GET /habit-tracker returns a successful response."""
    response = client.get("/habit-tracker")

    assert response.status_code == 200


def test_habit_tracker_post_creates_habit(client):
    """POST /habit-tracker creates and saves a habit."""
    habit_data = {
        "name": "Read 20 pages",
        "description": "Daily reading goal",
    }

    response = client.post(
        "/habit-tracker",
        data=habit_data,
        follow_redirects=False,
    )

    stored = Habit.query.filter_by(name="Read 20 pages").first()

    assert response.status_code == 302
    assert stored is not None
    assert stored.description == "Daily reading goal"