from dataclasses import dataclass
import json


@dataclass(frozen=True)
class RowRevision:
    site: str
    day: str
    row: str
    version: int
    parent: int | None
    changed: tuple[str, ...]


@dataclass(frozen=True)
class FrozenDay:
    site: str
    day: str
    version: int
    rows: tuple[RowRevision, ...]


@dataclass(frozen=True)
class FrozenBody:
    row: str
    column: str
    value: int | str | None
    origin: str
    correction: str | None
    entry_id: int


def initialize_revisions(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS frozen_days (
            version INTEGER PRIMARY KEY,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            UNIQUE (version, site, day)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS row_revisions (
            version INTEGER NOT NULL,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            row_key TEXT NOT NULL,
            parent INTEGER CHECK (parent IS NULL OR parent < version),
            changed TEXT NOT NULL,
            PRIMARY KEY (version, site, day, row_key),
            FOREIGN KEY (version, site, day) REFERENCES frozen_days(version, site, day),
            FOREIGN KEY (parent, site, day, row_key)
                REFERENCES row_revisions(version, site, day, row_key)
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS revisions_by_row
        ON row_revisions(site, day, row_key, version DESC)
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS frozen_bodies (
            version INTEGER NOT NULL,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            row_key TEXT NOT NULL,
            column_name TEXT NOT NULL CHECK (column_name IN ('meter', 'quality')),
            body TEXT NOT NULL,
            origin TEXT NOT NULL,
            correction TEXT,
            entry_id INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (version, site, day, row_key, column_name),
            FOREIGN KEY (version, site, day, row_key)
                REFERENCES row_revisions(version, site, day, row_key)
        )
    ''')


def append_frozen_day(db, site, day):
    cursor = db.execute('INSERT INTO frozen_days(site, day) VALUES (?, ?)', (site, day))
    return cursor.lastrowid


def append_row_revision(db, site, day, version, row, parent, entries):
    changed = tuple(entry.column for entry in entries)
    db.execute('''
        INSERT INTO row_revisions(version, site, day, row_key, parent, changed)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (version, site, day, row, parent, json.dumps(changed)))
    db.executemany('''
        INSERT INTO frozen_bodies(
            version, site, day, row_key, column_name, body, origin, correction, entry_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        (version, site, day, row, entry.column, json.dumps(entry.value, ensure_ascii=False),
         entry.origin.reference, entry.correction, entry.entry_id)
        for entry in entries
    ))
    return RowRevision(site, day, row, version, parent, changed)
