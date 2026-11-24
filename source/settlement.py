import json

from freeze.history import load_row_revision
from source.origins import require_identifier, require_number, validate_origin, validate_station_day
from source.quality import validate_verdict
from storage import write_transaction


_RECEIPT_FIELDS = {'schema', 'account', 'site', 'day', 'previous_account', 'rows'}
_ROW_FIELDS = {
    'row', 'version', 'meter_version', 'quality_version',
    'reading', 'verdict', 'meter_origin', 'quality_origin',
}
_RECEIPT_SCHEMA = 2


def initialize_settlement(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS settlement_receipts (
            account INTEGER PRIMARY KEY,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            previous_account INTEGER,
            canonical TEXT NOT NULL UNIQUE
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS settlement_receipt_rows (
            account INTEGER NOT NULL,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            row_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            meter_version INTEGER NOT NULL,
            quality_version INTEGER NOT NULL,
            reading TEXT NOT NULL,
            verdict TEXT NOT NULL,
            meter_origin TEXT NOT NULL,
            quality_origin TEXT NOT NULL,
            PRIMARY KEY (account, row_key),
            FOREIGN KEY (account) REFERENCES settlement_receipts(account)
        )
    ''')


def _unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate JSON field {key}')
        data[key] = value
    return data


def _decode(raw):
    if not isinstance(raw, str):
        raise ValueError('settlement receipt must be JSON text')
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise ValueError('settlement receipt is not valid JSON') from error
    if not isinstance(data, dict) or set(data) != _RECEIPT_FIELDS:
        raise ValueError('invalid settlement receipt fields')
    if type(data['schema']) is not int or data['schema'] != _RECEIPT_SCHEMA:
        raise ValueError('unsupported settlement receipt schema')
    return data


def _decode_rows(data):
    rows = data['rows']
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('settlement rows must be an array of objects')
    if not rows:
        raise ValueError('settlement receipt must publish at least one row')
    decoded = []
    seen = set()
    for row in rows:
        if set(row) != _ROW_FIELDS:
            raise ValueError('invalid settlement row fields')
        require_identifier(row['row'], 'settlement row')
        if row['row'] in seen:
            raise ValueError('duplicate settlement row')
        seen.add(row['row'])
        require_number(row['version'], 'row version')
        require_number(row['meter_version'], 'meter body version')
        require_number(row['quality_version'], 'quality body version')
        if row['meter_version'] > row['version'] or row['quality_version'] > row['version']:
            raise ValueError('column body version cannot follow its row version')
        reading = row['reading']
        if reading is not None and type(reading) is not int:
            raise ValueError('settlement reading must be integer liters or null')
        validate_verdict(row['verdict'])
        validate_origin(row['meter_origin'])
        validate_origin(row['quality_origin'])
        decoded.append(row)
    return decoded


def _lineage_body(db, site, day, row, start_version, column):
    """沿冻结沿用关系找到 start_version 实际使用的该栏正文，返回 (版次, 正文, 出处)。"""
    version = require_number(start_version, 'row version')
    while True:
        revision = db.execute('''
            SELECT parent, changed FROM row_revisions
            WHERE version = ? AND site = ? AND day = ? AND row_key = ?
        ''', (version, site, day, row)).fetchone()
        if revision is None:
            raise LookupError(f'row version {site}/{day}/{row}/{version} is not in the local freeze archive')
        if column in json.loads(revision['changed']):
            stored = db.execute('''
                SELECT body, origin FROM frozen_bodies
                WHERE version = ? AND site = ? AND day = ? AND row_key = ? AND column_name = ?
            ''', (version, site, day, row, column)).fetchone()
            if stored is None:
                raise ValueError('frozen lineage is missing its column body')
            return version, json.loads(stored['body']), stored['origin']
        if revision['parent'] is None:
            raise ValueError('frozen lineage has no body for the requested column')
        version = revision['parent']


def _verify_against_freeze(db, site, day, rows):
    """行版须在本地冻结档案中，栏正文身份、内容与沿用关系相符。"""
    for row in rows:
        load_row_revision(db, site, day, row['row'], row['version'])
        meter_version, reading, meter_origin = _lineage_body(
            db, site, day, row['row'], row['version'], 'meter')
        quality_version, verdict, quality_origin = _lineage_body(
            db, site, day, row['row'], row['version'], 'quality')
        if meter_version != row['meter_version'] or quality_version != row['quality_version']:
            raise ValueError('receipt column body identity disagrees with the frozen lineage')
        if (reading, meter_origin) != (row['reading'], row['meter_origin']):
            raise ValueError('receipt meter body disagrees with the frozen registration')
        if (verdict, quality_origin) != (row['verdict'], row['quality_origin']):
            raise ValueError('receipt quality body disagrees with the frozen registration')


def _canonical(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':'))


def admit_settlement(db, raw):
    """接收仓端出版结清回执文本，持久接纳后返回其固定账号。

    同账号同内容重投幂等返回原受理账号；格式错误、内容冲突、站日或正文身份
    不符抛 ValueError，已有受理记录与结清状态保持原样。
    """
    data = _decode(raw)
    account = require_number(data['account'], 'settlement account')
    validate_station_day(data['site'], data['day'])
    site, day = data['site'], data['day']
    previous = data['previous_account']
    if previous is not None:
        require_number(previous, 'previous account')
        if previous == account:
            raise ValueError('a receipt cannot be its own predecessor')
    rows = _decode_rows(data)
    _verify_against_freeze(db, site, day, rows)
    canonical = _canonical(data)

    with write_transaction(db):
        existing = db.execute(
            'SELECT canonical, site, day FROM settlement_receipts WHERE account = ?',
            (account,),
        ).fetchone()
        if existing is not None:
            if existing['canonical'] != canonical:
                raise ValueError(f'settlement account {account} already holds different content')
            return account
        # 链完整性：本站日至多一个首账；已受理前件至多一个后继（拒绝分叉）。
        # 前件尚未送达时无法判定后继冲突，待其送达后由首账/后继唯一性补齐拦截。
        if previous is None:
            other_head = db.execute('''
                SELECT account FROM settlement_receipts
                WHERE site = ? AND day = ? AND previous_account IS NULL AND account != ?
                LIMIT 1
            ''', (site, day, account)).fetchone()
            if other_head is not None:
                raise ValueError('another receipt already opens this station day prefix')
        else:
            predecessor = db.execute(
                'SELECT site, day FROM settlement_receipts WHERE account = ?',
                (previous,),
            ).fetchone()
            if predecessor is not None:
                if (predecessor['site'], predecessor['day']) != (site, day):
                    raise ValueError('predecessor receipt belongs to another station day')
                sibling = db.execute('''
                    SELECT account FROM settlement_receipts
                    WHERE site = ? AND day = ? AND previous_account = ? AND account != ?
                    LIMIT 1
                ''', (site, day, previous, account)).fetchone()
                if sibling is not None:
                    raise ValueError('another receipt already extends this station day predecessor')
        db.execute('''
            INSERT INTO settlement_receipts(account, site, day, previous_account, canonical)
            VALUES (?, ?, ?, ?, ?)
        ''', (account, site, day, previous, canonical))
        db.executemany('''
            INSERT INTO settlement_receipt_rows(
                account, site, day, row_key, version, meter_version, quality_version,
                reading, verdict, meter_origin, quality_origin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            (account, site, day, row['row'], row['version'], row['meter_version'],
             row['quality_version'], json.dumps(row['reading'], ensure_ascii=False),
             row['verdict'], row['meter_origin'], row['quality_origin'])
            for row in rows
        ))
        return account


def _successor(previous_of, account):
    successors = [candidate for candidate, previous in previous_of.items()
                  if previous == account]
    return successors[0] if successors else None


def _proven_prefix(db, site, day):
    """返回可证完整本站日出版前缀：(末账号或None, 链上账号由首到末有序元组)。

    回执可乱序送达：从首账（previous_account 为空）出发，沿直接后继逐账推进，
    前件尚未在本地受理处截断，得到当前可证的最长连续本站日出版前缀。
    同前件的分叉在受理时按冲突拦截。
    """
    records = db.execute('''
        SELECT account, previous_account FROM settlement_receipts
        WHERE site = ? AND day = ?
    ''', (site, day)).fetchall()
    if not records:
        return None, ()
    previous_of = {record['account']: record['previous_account'] for record in records}
    head = next((account for account, previous in previous_of.items() if previous is None), None)
    if head is None:
        return None, ()
    chain = []
    account = head
    while account in previous_of:
        chain.append(account)
        account = _successor(previous_of, account)
        if account is None:
            break
    return chain[-1], tuple(chain)


def _published_column_versions(db, site, day, chain):
    """链上每个账号对各 (行, 栏) 实际使用的栏正文版次，按出版顺序。"""
    if not chain:
        return {}
    placeholders = ','.join('?' for _ in chain)
    records = db.execute(f'''
        SELECT account, row_key, meter_version, quality_version
        FROM settlement_receipt_rows
        WHERE site = ? AND day = ? AND account IN ({placeholders})
    ''', (site, day, *chain)).fetchall()
    order = {account: index for index, account in enumerate(chain)}
    published = {}
    for record in records:
        for column, version in (
                ('meter', record['meter_version']),
                ('quality', record['quality_version'])):
            published.setdefault((record['row_key'], column), []).append(
                (order[record['account']], record['account'], version))
    for occurrences in published.values():
        occurrences.sort()
    return published


def read_settlement(db, *, site, day):
    """离线结清查询：末账号与本站日每条已冻结具名勘误改栏的结局。"""
    validate_station_day(site, day)
    last_account, chain = _proven_prefix(db, site, day)
    published = _published_column_versions(db, site, day, chain)
    records = db.execute('''
        SELECT entry_id, correction, row_key, column_name, archived_version
        FROM registrations
        WHERE site = ? AND day = ? AND archived_version IS NOT NULL
        ORDER BY entry_id
    ''', (site, day)).fetchall()
    entries = []
    for record in records:
        body_version, _, _ = _lineage_body(
            db, site, day, record['row_key'], record['archived_version'],
            record['column_name'])
        state, decided_at = 'pending', None
        for _, account, used_version in published.get(
                (record['row_key'], record['column_name']), ()):
            if used_version >= body_version:
                state = 'used' if used_version == body_version else 'skipped'
                decided_at = account
                break
        entries.append({
            'entry_id': record['entry_id'],
            'correction': record['correction'],
            'row': record['row_key'],
            'column': record['column_name'],
            'version': body_version,
            'state': state,
            'decided_at': decided_at,
        })
    return {'account': last_account, 'entries': entries}
