import pytest

# === Habit Tracker Tests ===

def test_habit_tracker_get_returns_ok(client):
    """Test that GET /habit-tracker returns a 200 status code."""
    response = client.get('/habit-tracker')

    assert response.status_code == 200


def test_habit_tracker_post_creates_habit(client, habits):
    """Test that POST /habit-tracker creates a new habit in the database."""
    habit_data = {
        'name': 'Read 20 pages',
        'description': 'Daily reading goal',
        'target': '1',
        'frequency': 'daily',
    }

    response = client.post(
        '/habit-tracker',
        data=habit_data,
        follow_redirects=False,
    )

    assert response.status_code == 302

    stored = habits.find_one({'name': 'Read 20 pages'})

    assert stored is not None
    assert stored['description'] == 'Daily reading goal'


# === Dashboard Tests ===


def test_dashboard_get_returns_ok(client):
    """Test that GET /dashboard returns a 200 status code."""
    response = client.get('/dashboard')

    assert response.status_code == 200
    assert b'Dashboard' in response.data
    assert b"Today's Habits" in response.data
    assert b'Total Habits' in response.data
    assert b'All My Habits' in response.data


def test_dashboard_displays_date_and_welcome(client):
    """Dashboard displays the supplied date and welcome message."""
    from flask import template_rendered

    captured_contexts = []

    def record_template(sender, template, context, **extra):
        if template.name == 'apps/habit_tracker/dashboard.html':
            captured_contexts.append(context)

    with template_rendered.connected_to(
        record_template,
        client.application,
    ):
        response = client.get('/dashboard')

    assert response.status_code == 200
    assert len(captured_contexts) == 1

    today = captured_contexts[0]['today']

    assert today
    assert f'Today: {today}'.encode() in response.data
    assert b'Welcome back!' in response.data

# === Login Tests ===

def test_login_get_returns_ok(client):
    """Test that the login page renders successfully."""
    response = client.get('/login')

    assert response.status_code == 200
    assert b'Log in to ' in response.data
    assert b'Email address' in response.data


def test_login_requires_both_fields(client):
    """Test that an incomplete login shows a helpful validation message."""
    response = client.post('/login', data={'email': '', 'password': ''})

    assert response.status_code == 200
    assert b'Enter both your email and password' in response.data


def test_login_redirects_to_dashboard_with_credentials(client):
    """Test the temporary demo login flow."""
    response = client.post(
        '/login',
        data={'email': 'person@example.com', 'password': 'password'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


# === Parametrized Tests ===

@pytest.mark.parametrize(
    'endpoint',
    ['/habit-tracker', '/dashboard'],
)
def test_all_pages_get_returns_ok(client, endpoint):
    """Test that Habit Tracker pages return 200 status code on GET requests."""
    response = client.get(endpoint)

    assert response.status_code == 200


# === Registration Tests ===

from datetime import datetime  # noqa: E402

from werkzeug.security import check_password_hash  # noqa: E402

REGISTER_DATA = {
    'username': 'newuser',
    'email': 'NewUser@Example.com',
    'password': 'password123',
    'confirm_password': 'password123',
}


def _users(mongo_client):
    return mongo_client['Habit-Tracker_test']['users']


def test_register_get_returns_ok(client):
    response = client.get('/register')
    assert response.status_code == 200
    assert b'Create an Account' in response.data


def test_register_post_creates_user_and_logs_in(client, mongo_client):
    response = client.post('/register', data=REGISTER_DATA, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == '/dashboard'
    assert b'Registration successful' in response.data

    user = _users(mongo_client).find_one({'username': 'newuser'})
    assert user is not None
    assert user['email'] == 'newuser@example.com'  # lowercased, same as login
    assert check_password_hash(user['password'], 'password123')
    assert isinstance(user['created_at'], datetime)

    with client.session_transaction() as sess:
        assert sess['user_id'] == str(user['_id'])
        assert sess['username'] == 'newuser'
        assert sess['user_email'] == 'newuser@example.com'


def test_registered_user_can_log_in(client):
    assert client.post('/register', data=REGISTER_DATA).status_code == 302
    client.post('/logout')

    response = client.post(
        '/login', data={'email': 'newuser@example.com', 'password': 'password123'}
    )
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


def test_register_creates_unique_indexes(client, mongo_client):
    client.post('/register', data=REGISTER_DATA)
    indexes = _users(mongo_client).index_information()
    assert indexes['email_unique']['unique'] is True
    assert indexes['username_unique']['unique'] is True
    assert indexes['username_unique']['sparse'] is True


def test_register_rejects_mismatched_passwords(client, mongo_client):
    data = {**REGISTER_DATA, 'confirm_password': 'different'}
    response = client.post('/register', data=data)
    assert response.status_code == 200
    assert b'Passwords do not match' in response.data
    assert _users(mongo_client).find_one({'username': 'newuser'}) is None


def test_register_rejects_short_password(client, mongo_client):
    data = {**REGISTER_DATA, 'password': 'short', 'confirm_password': 'short'}
    response = client.post('/register', data=data)
    assert response.status_code == 200
    assert b'at least 8 characters' in response.data
    assert _users(mongo_client).find_one({'username': 'newuser'}) is None


def test_register_rejects_duplicate_username(client, mongo_client):
    _users(mongo_client).insert_one({'username': 'newuser', 'email': 'other@example.com'})
    response = client.post('/register', data=REGISTER_DATA)
    assert response.status_code == 200
    assert b'Username is already taken' in response.data


def test_register_rejects_duplicate_email(client, mongo_client):
    _users(mongo_client).insert_one({'username': 'someone', 'email': 'newuser@example.com'})
    response = client.post('/register', data=REGISTER_DATA)
    assert response.status_code == 200
    assert b'Email is already registered' in response.data
    assert _users(mongo_client).count_documents({'email': 'newuser@example.com'}) == 1


def test_register_handles_duplicate_created_between_check_and_insert(
    client, mongo_client, monkeypatch
):
    """A concurrent registration that wins the race is caught by the unique index."""
    import app as app_module

    users = _users(mongo_client)
    app_module.ensure_user_indexes(users)
    real_hash = app_module.generate_password_hash

    def hash_after_competitor_registers(password):
        users.insert_one({'username': 'competitor', 'email': 'newuser@example.com'})
        return real_hash(password)

    monkeypatch.setattr(app_module, 'generate_password_hash', hash_after_competitor_registers)

    response = client.post('/register', data=REGISTER_DATA)
    assert response.status_code == 200
    assert b'Email is already registered' in response.data
    assert users.count_documents({'email': 'newuser@example.com'}) == 1
    with client.session_transaction() as sess:
        assert 'user_id' not in sess


def test_register_without_database_shows_error(client, monkeypatch):
    monkeypatch.delenv('MONGODB_URI', raising=False)
    response = client.post('/register', data=REGISTER_DATA)
    assert response.status_code == 200
    assert b'Database connection error' in response.data


def test_register_redirects_if_already_logged_in(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 'abc'
    response = client.get('/register')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


def test_navbar_shows_register_only_when_logged_out(client):
    assert b'Register' in client.get('/habit-tracker').data
    with client.session_transaction() as sess:
        sess['user_id'] = 'abc'
    assert b'Register' not in client.get('/dashboard').data
