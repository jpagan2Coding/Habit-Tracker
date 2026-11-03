"""Acceptance tests for the POST /habits/import endpoint.

Upload a UTF-8 CSV .habit attachment in the multipart ``file`` field.
The columns and one-row-per-completed-date layout mirror test_export_habits.py.
Successful imports redirect to the dashboard; invalid uploads return HTTP 400
without writing habits.
"""

import csv
import io
from datetime import datetime

import pytest
from bson import ObjectId

from models import HABIT_FILE_FIELDS


@pytest.fixture
def import_client(client):
    with client.session_transaction() as session:
        session["user_id"] = "test-user"
        session["user_email"] = "person@example.com"
    return client


@pytest.fixture
def import_rows():
    details = {
        "habit_id": str(ObjectId()),
        "name": "Read daily",
        "description": "Read 20 pages",
        "target": "1",
        "frequency": "daily",
        "created_at": "2026-10-01T14:30:45Z",
    }
    return [
        {**details, "completed_date": date} for date in ["2026-10-02", "2026-10-05", "2026-10-30"]
    ]


def encode_habit(rows, fields=HABIT_FILE_FIELDS):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def upload_habit(client, payload, filename="Read_daily.habit"):
    return client.post(
        "/habits/import",
        data={"file": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


def assert_import_redirect(response):
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_import_restores_details_and_entire_completion_history(import_client, habits, import_rows):
    response = upload_habit(import_client, encode_habit(import_rows))

    assert_import_redirect(response)
    assert habits.count_documents({}) == 1
    habit = habits.find_one()
    assert habit["name"] == "Read daily"
    assert habit["description"] == "Read 20 pages"
    assert habit["target"] == 1
    assert habit["frequency"] == "daily"
    assert habit["created_at"] == datetime(2026, 10, 1, 14, 30, 45)
    assert habit["completed_dates"] == [row["completed_date"] for row in import_rows]
    assert habit["user_email"] == "person@example.com"
    assert habit["_id"] != ObjectId(import_rows[0]["habit_id"])


def test_import_preserves_habit_without_history(import_client, habits, import_rows):
    row = {**import_rows[0], "completed_date": ""}

    response = upload_habit(import_client, encode_habit([row]))

    assert_import_redirect(response)
    assert habits.count_documents({}) == 1
    assert habits.find_one()["completed_dates"] == []


def test_import_restores_skipped_history(import_client, habits, import_rows):
    rows = [
        {**import_rows[0], "status": "completed"},
        {**import_rows[1], "status": "skipped"},
    ]

    response = upload_habit(import_client, encode_habit(rows))

    assert_import_redirect(response)
    habit = habits.find_one()
    assert habit["completed_dates"] == ["2026-10-02"]
    assert habit["skipped_dates"] == ["2026-10-05"]


def test_import_preserves_csv_special_characters_and_unicode(import_client, habits, import_rows):
    details = {
        "name": 'Lire, "chaque jour" 📚',
        "description": 'Café\nChapter 1, "start"\r\n続ける',
    }
    rows = [{**row, **details} for row in import_rows]

    response = upload_habit(import_client, encode_habit(rows))

    assert_import_redirect(response)
    habit = habits.find_one()
    assert habit["name"] == details["name"]
    assert habit["description"] == details["description"]


def test_import_does_not_overwrite_another_users_habit(import_client, habits, import_rows):
    existing = {
        "_id": ObjectId(import_rows[0]["habit_id"]),
        "name": "Private habit",
        "user_email": "someone-else@example.com",
        "completed_dates": [],
    }
    habits.insert_one(existing.copy())

    response = upload_habit(import_client, encode_habit(import_rows))

    assert_import_redirect(response)
    assert habits.find_one({"_id": existing["_id"]}) == existing
    assert habits.count_documents({}) == 2
    imported = habits.find_one({"user_email": "person@example.com"})
    assert imported["name"] == "Read daily"
    assert imported["_id"] != existing["_id"]


@pytest.mark.parametrize("missing", ["user_id", "user_email", "both"])
def test_import_requires_login(import_client, habits, import_rows, missing):
    with import_client.session_transaction() as session:
        if missing == "both":
            session.clear()
        else:
            session.pop(missing)

    response = upload_habit(import_client, encode_habit(import_rows))

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert habits.count_documents({}) == 0


@pytest.mark.parametrize(
    "payload", [b"", b"\xff\xfe\xfa", b"not,a,habit\n1,2,3\n", encode_habit([])]
)
def test_import_rejects_empty_or_invalid_files(import_client, habits, payload):
    response = upload_habit(import_client, payload)

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


@pytest.mark.parametrize("filename", ["", "Read_daily.txt"])
def test_import_rejects_invalid_filename(import_client, habits, import_rows, filename):
    response = upload_habit(import_client, encode_habit(import_rows), filename)

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


def test_import_requires_file_attachment(import_client, habits):
    response = import_client.post("/habits/import", data={})

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


@pytest.mark.parametrize("field", HABIT_FILE_FIELDS)
def test_import_requires_export_columns(import_client, habits, import_rows, field):
    fields = [name for name in HABIT_FILE_FIELDS if name != field]
    rows = [{key: value for key, value in row.items() if key != field} for row in import_rows]

    response = upload_habit(import_client, encode_habit(rows, fields))

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("target", "not-a-number"),
        ("target", "0"),
        ("target", "-1"),
        ("frequency", "unsupported"),
        ("created_at", "not-a-timestamp"),
        ("completed_date", "2026-02-30"),
    ],
)
def test_import_rejects_invalid_values_without_partial_writes(
    import_client, habits, import_rows, field, value
):
    # A valid first row must not be committed before a later error is found.
    rows = [import_rows[0], {**import_rows[1], field: value}]

    response = upload_habit(import_client, encode_habit(rows))

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


def test_exported_habit_can_be_imported_by_another_user(import_client, habits):
    original = {
        "name": "Read daily",
        "description": "Read 20 pages",
        "target": 1,
        "frequency": "daily",
        "created_at": datetime(2026, 10, 1, 14, 30, 45),
        "completed_dates": ["2026-10-02", "2026-10-05"],
        "user_email": "person@example.com",
    }
    original["_id"] = habits.insert_one(original.copy()).inserted_id
    exported = import_client.get(f"/habits/{original['_id']}/export")
    assert exported.status_code == 200
    with import_client.session_transaction() as session:
        session["user_id"] = "another-user"
        session["user_email"] = "another@example.com"

    response = upload_habit(import_client, exported.data)

    assert_import_redirect(response)
    assert habits.count_documents({}) == 2
    imported = habits.find_one({"user_email": "another@example.com"})
    for field in ["name", "description", "target", "frequency", "created_at", "completed_dates"]:
        assert imported[field] == original[field]
    assert imported["_id"] != original["_id"]
    assert habits.find_one({"_id": original["_id"]}) == original


@pytest.mark.parametrize(
    "field", ["habit_id", "name", "description", "target", "frequency", "created_at"]
)
def test_import_rejects_inconsistent_habit_details(import_client, habits, import_rows, field):
    rows = [import_rows[0], {**import_rows[1], field: "different"}]

    response = upload_habit(import_client, encode_habit(rows))

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


@pytest.mark.parametrize(
    "row",
    [
        "id,name,description,1,daily,2026-10-01T00:00:00Z",
        "id,name,description,1,daily,2026-10-01T00:00:00Z,,extra",
        '"unterminated',
    ],
)
def test_import_rejects_malformed_csv_rows(import_client, habits, row):
    payload = (",".join(HABIT_FILE_FIELDS) + "\n" + row).encode()

    response = upload_habit(import_client, payload)

    assert response.status_code == 400
    assert habits.count_documents({}) == 0


def test_import_normalizes_timezone_and_duplicate_dates(import_client, habits, import_rows):
    rows = [
        {**row, "created_at": "2026-10-01T10:30:45-04:00"} for row in [*import_rows, import_rows[0]]
    ]

    response = upload_habit(import_client, encode_habit(rows))

    assert_import_redirect(response)
    habit = habits.find_one()
    assert habit["created_at"] == datetime(2026, 10, 1, 14, 30, 45)
    assert habit["completed_dates"] == [row["completed_date"] for row in import_rows]


def test_dashboard_offers_habit_upload(import_client):
    response = import_client.get("/dashboard")

    assert response.status_code == 200
    assert b'action="/habits/import"' in response.data
    assert b'enctype="multipart/form-data"' in response.data
    assert b'name="file"' in response.data
