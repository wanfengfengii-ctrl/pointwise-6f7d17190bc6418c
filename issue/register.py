from issue.envelope import ColumnEnvelope, RevisionEvidence, encode_envelope
from issue.meter import build_meter_rows
from issue.quality import build_quality_rows
from source.origins import require_identifier, require_number
from storage import open_database, write_transaction


_ISSUERS = {
    'meter': (0x5143494D, build_meter_rows),
    'quality': (0x51434951, build_quality_rows),
}


def _issuer(column):
    if column not in ('meter', 'quality'):
        raise ValueError('unknown issue column')
    return _ISSUERS[column]


def _initialize(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS issues (
            item_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            column_name TEXT NOT NULL CHECK (column_name IN ('meter', 'quality')),
            original TEXT NOT NULL
        )
    ''')


def open_issuer(path, column):
    identity, _ = _issuer(column)
    return open_database(path, identity, _initialize)


def _original(db, item_id):
    return db.execute('SELECT * FROM issues WHERE item_id = ?', (item_id,)).fetchone()


def load_original_issue(db, item_id):
    require_identifier(item_id, 'item number')
    original = _original(db, item_id)
    if original is None:
        raise LookupError(f'unknown issue {item_id}')
    return original['original']


def _required_version(index, target, column):
    scope = target
    while column not in scope.changed:
        if scope.parent is None or scope.parent >= scope.version:
            raise ValueError('incomplete frozen lineage in issue projection')
        parent = index.get((scope.row, scope.parent))
        if parent is None:
            raise ValueError('missing parent scope in issue projection')
        scope = parent
    return scope.version


def _revision_evidence(projection):
    index = {}
    targets = []
    for scope in projection.revisions:
        key = (scope.row, scope.version)
        if (scope.site, scope.day) != (projection.site, projection.day) or key in index:
            raise ValueError('inconsistent frozen scope in issue projection')
        index[key] = scope
        if scope.version == projection.version:
            targets.append(scope)
    if not targets:
        raise ValueError('projection has no target rows')
    evidence = tuple(
        RevisionEvidence(scope.row, scope.parent,
                         _required_version(index, scope, 'meter'),
                         _required_version(index, scope, 'quality'))
        for scope in sorted(targets, key=lambda target: target.row)
    )
    expected = {row.row for row in evidence if row.required_version(projection.column) == projection.version}
    body_keys = {body.row for body in projection.bodies}
    if body_keys != expected or len(projection.bodies) != len(expected):
        raise ValueError('projection bodies do not match its frozen range')
    return evidence


def issue_column(db, version, column, item_id, *, projection=None):
    identity, build_rows = _issuer(column)
    require_number(version, 'day version')
    require_identifier(item_id, 'item number')
    if db.execute('PRAGMA application_id').fetchone()[0] != identity:
        raise ValueError('issuer database belongs to a different column')
    with write_transaction(db):
        original = _original(db, item_id)
        if original is not None:
            if (original['version'], original['column_name']) != (version, column):
                raise ValueError('item number already belongs to another issue request')
            return original['original']
        if projection is None or (projection.version, projection.column) != (version, column):
            raise ValueError('a matching frozen column projection is required')
        item = ColumnEnvelope(
            item_id, projection.site, projection.day, version, column,
            build_rows(projection), _revision_evidence(projection),
        )
        encoded = encode_envelope(item)
        db.execute('''
            INSERT INTO issues(item_id, version, column_name, original) VALUES (?, ?, ?, ?)
        ''', (item_id, version, column, encoded))
        return encoded
