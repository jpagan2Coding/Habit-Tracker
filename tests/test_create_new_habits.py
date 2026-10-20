from models import Habit

VALID_HABIT = {
    'name': 'Drink Water',
    'description': '8 glasses a day',
    'target': '3',
    'frequency': 'weekly',
}


# Acceptance criterion 1: a complete habit is saved and shows on the dashboard

def test_create_new_habit_saves_all_fields(client):
    response = client.post('/habit-tracker', data=VALID_HABIT)

    assert response.status_code == 302
    habit = Habit.query.filter_by(name='Drink Water').first()
    assert habit is not None
    assert habit.target == 3
    assert habit.frequency == 'weekly'


def test_saved_habit_appears_on_dashboard(client):
    response = client.post('/habit-tracker', data=VALID_HABIT, follow_redirects=True)

    assert response.request.path == '/dashboard'
    assert b'Drink Water' in response.data


# Acceptance criterion 2: a missing required field means nothing is saved

def test_missing_name_does_not_create_habit(client):
    response = client.post('/habit-tracker', data={**VALID_HABIT, 'name': '   '})

    assert response.status_code == 400
    assert Habit.query.count() == 0


def test_missing_target_does_not_create_habit(client):
    response = client.post('/habit-tracker', data={**VALID_HABIT, 'target': ''})

    assert response.status_code == 400
    assert Habit.query.count() == 0


def test_missing_frequency_does_not_create_habit(client):
    response = client.post('/habit-tracker', data={**VALID_HABIT, 'frequency': ''})

    assert response.status_code == 400
    assert Habit.query.count() == 0