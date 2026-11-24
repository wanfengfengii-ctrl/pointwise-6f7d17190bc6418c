import json

from freeze.revisions import FrozenBody, FrozenDay, RowRevision
from source.origins import require_number


def _revision(record):
    return RowRevision(
        record['site'], record['day'], record['row_key'], record['version'],
        record['parent'], tuple(json.loads(record['changed'])),
    )


def load_frozen_day(db, version):
    require_number(version, 'day version')
    day = db.execute('SELECT site, day FROM frozen_days WHERE version = ?', (version,)).fetchone()
    if day is None:
        raise LookupError(f'unknown frozen day version {version}')
    records = db.execute('SELECT * FROM row_revisions WHERE version = ? ORDER BY row_key', (version,))
    return FrozenDay(day['site'], day['day'], version, tuple(_revision(record) for record in records))


def load_row_revision(db, site, day, row, version):
    require_number(version, 'row version')
    stored = db.execute('''
        SELECT * FROM row_revisions
        WHERE version = ? AND site = ? AND day = ? AND row_key = ?
    ''', (version, site, day, row)).fetchone()
    if stored is None:
        raise LookupError(f'unknown frozen row {site}/{day}/{row}/{version}')
    return _revision(stored)


def find_parent_revisions(db, site, day, rows):
    parents = {}
    for row in rows:
        stored = db.execute('''
            SELECT * FROM row_revisions
            WHERE site = ? AND day = ? AND row_key = ? ORDER BY version DESC LIMIT 1
        ''', (site, day, row)).fetchone()
        if stored is not None:
            parents[row] = _revision(stored)
    return parents


def load_frozen_bodies(db, version, column):
    records = db.execute('''
        SELECT row_key, column_name, body, origin, correction, entry_id
        FROM frozen_bodies WHERE version = ? AND column_name = ? ORDER BY row_key
    ''', (version, column))
    return tuple(
        FrozenBody(record['row_key'], record['column_name'], json.loads(record['body']),
                   record['origin'], record['correction'], record['entry_id'])
        for record in records
    )
