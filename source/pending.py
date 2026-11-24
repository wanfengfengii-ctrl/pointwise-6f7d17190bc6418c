from dataclasses import dataclass
import json

from freeze.revisions import initialize_revisions
from source.origins import SourceOrigin
from source.settlement import (
    admit_settlement,
    initialize_settlement,
    read_settlement,
)
from storage import open_database


@dataclass(frozen=True)
class PendingEntry:
    entry_id: int
    site: str
    day: str
    row: str
    column: str
    value: int | str | None
    origin: SourceOrigin
    correction: str | None
    archived_version: int | None = None


def _initialize(db):
    initialize_revisions(db)
    initialize_settlement(db)
    db.execute('''
        CREATE TABLE IF NOT EXISTS registrations (
            entry_id INTEGER PRIMARY KEY,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            row_key TEXT NOT NULL,
            column_name TEXT NOT NULL CHECK (column_name IN ('meter', 'quality')),
            body TEXT NOT NULL,
            origin TEXT NOT NULL,
            correction TEXT,
            archived_version INTEGER,
            FOREIGN KEY (archived_version, site, day) REFERENCES frozen_days(version, site, day)
        )
    ''')
    db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS initial_registration
        ON registrations(site, day, row_key, column_name) WHERE correction IS NULL
    ''')
    db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS corrected_registration
        ON registrations(correction, site, day, row_key, column_name)
        WHERE correction IS NOT NULL
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS unarchived_registrations
        ON registrations(site, day, entry_id) WHERE archived_version IS NULL
    ''')


def open_source(path):
    return open_database(path, 0x51435352, _initialize)


def save_pending(db, site, day, row, column, value, origin, *, correction=None):
    cursor = db.execute('''
        INSERT INTO registrations(site, day, row_key, column_name, body, origin, correction)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (site, day, row, column, json.dumps(value, ensure_ascii=False), origin.reference, correction))
    return PendingEntry(cursor.lastrowid, site, day, row, column, value, origin, correction)


def has_original_row(db, site, day, row):
    return db.execute('''
        SELECT 1 FROM registrations
        WHERE site = ? AND day = ? AND row_key = ? AND correction IS NULL
        LIMIT 1
    ''', (site, day, row)).fetchone() is not None


def correction_day(db, correction):
    bound = db.execute('''
        SELECT site, day FROM registrations WHERE correction = ? LIMIT 1
    ''', (correction,)).fetchone()
    return tuple(bound) if bound is not None else None


def _entry(record):
    return PendingEntry(
        record['entry_id'], record['site'], record['day'], record['row_key'],
        record['column_name'], json.loads(record['body']), SourceOrigin(record['origin']),
        record['correction'], record['archived_version'],
    )


def load_entry(db, entry_id):
    stored = db.execute('SELECT * FROM registrations WHERE entry_id = ?', (entry_id,)).fetchone()
    if stored is None:
        raise LookupError(f'unknown registration {entry_id}')
    return _entry(stored)


def list_pending(db, site, day):
    records = db.execute('''
        SELECT * FROM registrations
        WHERE site = ? AND day = ? AND archived_version IS NULL ORDER BY entry_id
    ''', (site, day))
    return tuple(_entry(record) for record in records)


def mark_frozen(db, version, entries):
    for entry in entries:
        cursor = db.execute('''
            UPDATE registrations SET archived_version = ?
            WHERE entry_id = ? AND site = ? AND day = ? AND archived_version IS NULL
        ''', (version, entry.entry_id, entry.site, entry.day))
        if cursor.rowcount != 1:
            raise ValueError(f'registration {entry.entry_id} is no longer pending')
