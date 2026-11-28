"""Local app passcode storage and validation.

Passcodes protect a signed-in session from other people using the same
device. They are deliberately kept *off* the cloud database: each passcode
is hashed with Werkzeug's ``generate_password_hash`` (the same salted scrypt
hashing used for account passwords) and the hash is written to a SQLite file
on the machine running the app. The plain-text passcode is never stored or
logged. See docs/SECURITY.md for the full behaviour.
"""

import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

PASSCODE_MIN_LENGTH = 4
PASSCODE_MAX_LENGTH = 12
PASSCODE_PATTERN = re.compile(rf"\d{{{PASSCODE_MIN_LENGTH},{PASSCODE_MAX_LENGTH}}}")
PASSCODE_RULE = (
    f"Passcode must be {PASSCODE_MIN_LENGTH} to {PASSCODE_MAX_LENGTH} digits (numbers only)."
)


def validate_new_passcode(passcode, confirm):
    """Return an error message for an invalid new passcode, or None when it is acceptable."""
    if not passcode:
        return "Enter a new passcode."
    if not PASSCODE_PATTERN.fullmatch(passcode):
        return PASSCODE_RULE
    if not confirm:
        return "Confirm your new passcode."
    if passcode != confirm:
        return "Passcodes do not match."
    return None


class LocalPasscodeStore:
    """Hashed passcodes kept in a local SQLite file, one per user id."""

    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        connection = sqlite3.connect(self.path)
        if is_new:
            # Only the account running the app should be able to read the hashes.
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_passcodes (
                user_id TEXT PRIMARY KEY,
                passcode_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        return connection

    def _stored_hash(self, user_id):
        # Reading must not create the file, so a fresh install stays empty.
        if not user_id or not self.path.exists():
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT passcode_hash FROM app_passcodes WHERE user_id = ?", (str(user_id),)
            ).fetchone()
        return row[0] if row else None

    def has_passcode(self, user_id):
        return self._stored_hash(user_id) is not None

    def verify(self, user_id, passcode):
        stored_hash = self._stored_hash(user_id)
        if stored_hash is None or not passcode:
            return False
        return check_password_hash(stored_hash, passcode)

    def set(self, user_id, passcode):
        """Hash and save a passcode, replacing any existing one for this user."""
        passcode_hash = generate_password_hash(passcode)
        updated_at = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO app_passcodes (user_id, passcode_hash, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    passcode_hash = excluded.passcode_hash,
                    updated_at = excluded.updated_at
                """,
                (str(user_id), passcode_hash, updated_at),
            )

    def remove(self, user_id):
        if not user_id or not self.path.exists():
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM app_passcodes WHERE user_id = ?", (str(user_id),))
