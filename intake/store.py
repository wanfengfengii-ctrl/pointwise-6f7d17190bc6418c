from contextlib import contextmanager

from ledger.snapshots import initialize_ledger
from storage import open_database, write_transaction


def initialize_intake(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            receipt_id INTEGER PRIMARY KEY,
            item_id TEXT NOT NULL UNIQUE,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            version INTEGER NOT NULL,
            column_name TEXT NOT NULL CHECK (column_name IN ('meter', 'quality')),
            canonical TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS receipts_by_day ON receipts(site, day, receipt_id)
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS original_material (
            receipt_id INTEGER PRIMARY KEY REFERENCES receipts(receipt_id),
            raw TEXT NOT NULL
        )
    ''')


def _initialize(db):
    initialize_intake(db)
    initialize_ledger(db)


def open_warehouse(path):
    return open_database(path, 0x51435748, _initialize)


@contextmanager
def warehouse_transaction(db):
    with write_transaction(db):
        yield
