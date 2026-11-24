from contextlib import contextmanager
import sqlite3


@contextmanager
def write_transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


def open_database(path, application_id, initialize):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with write_transaction(db):
            identity = db.execute('PRAGMA application_id').fetchone()[0]
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if (identity, version) == (0, 0):
                existing = db.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
                if existing is not None:
                    raise ValueError('database contains an unrecognized schema')
                db.execute(f'PRAGMA application_id = {application_id}')
                db.execute('PRAGMA user_version = 1')
            elif (identity, version) != (application_id, 1):
                raise ValueError('database role or schema version does not match')
            initialize(db)
    except BaseException:
        db.close()
        raise
    return db
