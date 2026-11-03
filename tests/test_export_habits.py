"""Acceptance tests for the GET /habits/<habit_id>/export endpoint.

The .habit attachment contains UTF-8 CSV, with one row per completed date.
Habits without history still have one row, with an empty completed_date.
See docs/HABIT_EXPORT.md for the file contract.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from werkzeug.http import parse_options_header

from models import HABIT_FILE_FIELDS


@pytest.fixture
def export_habit(client, habits):
    with client.session_transaction() as session:
        session['user_id'] = 'test-user'
        session['user_email'] = 'person@example.com'
    habit = {
        'name': 'Read daily',
        'description': 'Read 20 pages',
        'target': 1,
        'frequency': 'daily',
        'user_email': 'person@example.com',
        'created_at': datetime(2026, 10, 1, 14, 30, 45),
        'completed_dates': ['2026-10-02', '2026-10-05', '2026-10-30'],
    }
    habit['_id'] = habits.insert_one(habit).inserted_id
    return habit


def export_url(habit):
    return f"/habits/{habit['_id']}/export"


def read_export(response):
    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.data.decode('utf-8'), newline=''))
    assert reader.fieldnames == HABIT_FILE_FIELDS
    rows = list(reader)
    assert all(set(row) == set(HABIT_FILE_FIELDS) for row in rows)
    return rows


def test_export_downloads_csv_with_habit_extension(client, export_habit):
    response = client.get(export_url(export_habit))

    assert response.status_code == 200
    assert response.mimetype == 'text/csv'
    assert response.mimetype_params.get('charset', '').lower() == 'utf-8'
    disposition, options = parse_options_header(response.headers['Content-Disposition'])
    assert disposition == 'attachment'
    assert options['filename'].endswith('.habit')


def test_export_contains_all_details_and_entire_completion_history(client, export_habit):
    rows = read_export(client.get(export_url(export_habit)))

    expected_details = {
        'habit_id': str(export_habit['_id']),
        'name': 'Read daily',
        'description': 'Read 20 pages',
        'target': '1',
        'frequency': 'daily',
        'created_at': '2026-10-01T14:30:45Z',
    }
    assert rows == [
        {**expected_details, 'completed_date': date}
        for date in export_habit['completed_dates']
    ]


@pytest.mark.parametrize('history', [[], None])
def test_export_preserves_habit_without_history(client, habits, export_habit, history):
    if history is None:
        habits.update_one({'_id': export_habit['_id']}, {'$unset': {'completed_dates': ''}})
    else:
        habits.update_one({'_id': export_habit['_id']}, {'$set': {'completed_dates': history}})

    rows = read_export(client.get(export_url(export_habit)))

    assert len(rows) == 1
    assert rows[0]['habit_id'] == str(export_habit['_id'])
    assert rows[0]['name'] == export_habit['name']
    assert rows[0]['completed_date'] == ''


def test_export_round_trips_csv_special_characters_and_unicode(client, habits, export_habit):
    details = {'name': 'Lire, "chaque jour" 📚', 'description': 'Café\nChapter 1, "start"\r\n続ける'}
    habits.update_one({'_id': export_habit['_id']}, {'$set': details})

    rows = read_export(client.get(export_url(export_habit)))

    assert len(rows) == len(export_habit['completed_dates'])
    for row in rows:
        assert row['name'] == details['name']
        assert row['description'] == details['description']


def test_export_serializes_absent_description_as_empty(client, habits, export_habit):
    habits.update_one({'_id': export_habit['_id']}, {'$set': {'description': None}})

    rows = read_export(client.get(export_url(export_habit)))

    assert all(row['description'] == '' for row in rows)


def test_export_requires_login(client, export_habit):
    with client.session_transaction() as session:
        session.clear()

    response = client.get(export_url(export_habit))

    assert response.status_code == 302
    assert response.headers['Location'].split('?')[0].endswith('/login')
    assert 'Content-Disposition' not in response.headers


def test_export_does_not_expose_another_users_habit(client, habits, export_habit):
    habits.update_one(
        {'_id': export_habit['_id']}, {'$set': {'user_email': 'someone-else@example.com'}}
    )

    response = client.get(export_url(export_habit))

    assert response.status_code == 404
    assert export_habit['name'].encode() not in response.data
    assert 'Content-Disposition' not in response.headers


@pytest.mark.parametrize('habit_id', ['not-an-object-id', str(ObjectId())])
def test_export_unknown_or_invalid_id_returns_not_found(client, export_habit, habit_id):
    response = client.get(f'/habits/{habit_id}/export')

    assert response.status_code == 404
    assert 'Content-Disposition' not in response.headers


def test_export_does_not_modify_stored_habit(client, habits, export_habit):
    before = habits.find_one({'_id': export_habit['_id']})

    response = client.get(export_url(export_habit))

    assert response.status_code == 200
    assert habits.find_one({'_id': export_habit['_id']}) == before


def test_dashboard_links_to_export_in_both_habit_lists(client, export_habit):
    response = client.get('/dashboard')

    assert response.status_code == 200
    assert response.data.count(f'href="{export_url(export_habit)}"'.encode()) == 2
    assert b'aria-label="Export Read daily"' in response.data


def test_export_normalizes_timezone_to_utc(client, monkeypatch, export_habit):
    # Preserve timezone information here; MongoDB normally returns naive UTC.
    export_habit['created_at'] = datetime(
        2026, 10, 1, 10, 30, 45, tzinfo=timezone(timedelta(hours=-4))
    )
    monkeypatch.setattr('app._owned_habit', lambda habit_id: ({}, export_habit))

    rows = read_export(client.get(export_url(export_habit)))

    assert rows[0]['created_at'] == '2026-10-01T14:30:45Z'


def test_export_requires_email_in_session(client, export_habit):
    with client.session_transaction() as session:
        session.pop('user_email')

    response = client.get(export_url(export_habit))

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login')
