"""Regression tests for multi-check-in habit exports and imports."""

import csv
import io
from datetime import datetime, timezone

from bson import ObjectId

from progress import completion_count_on


def test_multiple_completions_survive_export_import(
    authenticated_client, habits
):
    """Exporting and importing preserves individual check-in counts."""
    original = {
        "name": "Brush Teeth",
        "description": "Morning and evening",
        "target": 2,
        "frequency": "daily",
        "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "user_email": "person@example.com",
        "completed_dates": ["2026-11-17", "2026-11-18"],
        "skipped_dates": [],
        "completion_history": [
            {
                "date": "2026-11-17",
                "status": "completed",
                "count": 2,
            },
            {
                "date": "2026-11-18",
                "status": "completed",
                "count": 1,
            },
        ],
    }

    habit_id = habits.insert_one(original).inserted_id

    exported = authenticated_client.get(
        f"/habits/{habit_id}/export"
    )

    assert exported.status_code == 200

    reader = csv.DictReader(
        io.StringIO(exported.data.decode("utf-8"))
    )
    rows = list(reader)

    assert "count" in reader.fieldnames
    assert [row["count"] for row in rows] == ["2", "1"]

    imported_response = authenticated_client.post(
        "/habits/import",
        data={
            "file": (
                io.BytesIO(exported.data),
                "brush-teeth.habit",
            )
        },
        content_type="multipart/form-data",
    )

    assert imported_response.status_code == 302

    imported = habits.find_one({
        "_id": {"$ne": habit_id},
        "user_email": "person@example.com",
    })

    assert imported is not None
    assert imported["target"] == 2

    assert completion_count_on(
        imported, datetime(2026, 11, 17).date()
    ) == 2

    assert completion_count_on(
        imported, datetime(2026, 11, 18).date()
    ) == 1


def test_legacy_export_keeps_original_columns(
    authenticated_client, habits
):
    """Habits with one check-in per day keep the old file format."""
    from models import HABIT_FILE_FIELDS

    habit_id = habits.insert_one({
        "name": "Drink Water",
        "description": "",
        "target": 1,
        "frequency": "daily",
        "created_at": datetime(2026, 11, 1),
        "user_email": "person@example.com",
        "completed_dates": ["2026-11-17"],
    }).inserted_id

    response = authenticated_client.get(
        f"/habits/{habit_id}/export"
    )

    reader = csv.DictReader(
        io.StringIO(response.data.decode("utf-8"))
    )

    assert reader.fieldnames == HABIT_FILE_FIELDS
    assert list(reader)[0]["completed_date"] == "2026-11-17"


def test_invalid_completion_count_is_rejected(
    authenticated_client, habits
):
    """Invalid counts cannot create imported habits."""
    rows = [
        {
            "habit_id": str(ObjectId()),
            "name": "Brush Teeth",
            "description": "",
            "target": "2",
            "frequency": "daily",
            "created_at": "2026-11-01T00:00:00Z",
            "completed_date": "2026-11-17",
            "status": "completed",
            "count": "-1",
        }
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

    response = authenticated_client.post(
        "/habits/import",
        data={
            "file": (
                io.BytesIO(output.getvalue().encode("utf-8")),
                "invalid.habit",
            )
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert habits.count_documents({}) == 0
