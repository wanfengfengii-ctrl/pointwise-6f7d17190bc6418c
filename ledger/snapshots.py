from dataclasses import dataclass
import json

from ledger.eligibility import EligibleRow
from ledger.totals import LedgerTotals


@dataclass(frozen=True)
class LedgerSnapshot:
    account: int | None
    site: str
    day: str
    rows: tuple[EligibleRow, ...]
    totals: LedgerTotals


def initialize_ledger(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS published_rows (
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            row_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            reading TEXT NOT NULL,
            verdict TEXT NOT NULL,
            meter_version INTEGER NOT NULL,
            meter_origin TEXT NOT NULL,
            quality_version INTEGER NOT NULL,
            quality_origin TEXT NOT NULL,
            PRIMARY KEY (site, day, row_key, version)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS ledger_snapshots (
            account INTEGER PRIMARY KEY,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            measured_count INTEGER NOT NULL,
            liters TEXT NOT NULL,
            UNIQUE (account, site, day)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS snapshot_rows (
            account INTEGER NOT NULL,
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            row_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (account, row_key),
            FOREIGN KEY (account, site, day) REFERENCES ledger_snapshots(account, site, day),
            FOREIGN KEY (site, day, row_key, version)
                REFERENCES published_rows(site, day, row_key, version)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS current_ledgers (
            site TEXT NOT NULL,
            day TEXT NOT NULL,
            account INTEGER NOT NULL,
            PRIMARY KEY (site, day),
            FOREIGN KEY (account, site, day) REFERENCES ledger_snapshots(account, site, day)
        )
    ''')


def save_snapshot(db, site, day, rows, totals, advances):
    db.executemany('''
        INSERT INTO published_rows(
            site, day, row_key, version, reading, verdict,
            meter_version, meter_origin, quality_version, quality_origin
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        (site, day, row.row, row.version, json.dumps(row.reading), row.verdict,
         row.meter_version, row.meter_origin, row.quality_version, row.quality_origin)
        for row in advances
    ))
    saved = db.execute('''
        INSERT INTO ledger_snapshots(site, day, measured_count, liters) VALUES (?, ?, ?, ?)
    ''', (site, day, totals.measured_count, str(totals.total_liters)))
    account = saved.lastrowid
    db.executemany('''
        INSERT INTO snapshot_rows(account, site, day, row_key, version) VALUES (?, ?, ?, ?, ?)
    ''', ((account, site, day, row.row, row.version) for row in rows))
    return LedgerSnapshot(account, site, day, rows, totals)


def advance_current(db, snapshot):
    db.execute('''
        INSERT INTO current_ledgers(site, day, account) VALUES (?, ?, ?)
        ON CONFLICT(site, day) DO UPDATE SET account = excluded.account
    ''', (snapshot.site, snapshot.day, snapshot.account))


def load_snapshot(db, account):
    stored = db.execute('SELECT * FROM ledger_snapshots WHERE account = ?', (account,)).fetchone()
    if stored is None:
        raise LookupError(f'unknown ledger account {account}')
    records = db.execute('''
        SELECT detail.row_key, detail.version, detail.reading, detail.verdict,
               detail.meter_version, detail.meter_origin, detail.quality_version, detail.quality_origin
        FROM snapshot_rows AS membership
        JOIN published_rows AS detail USING (site, day, row_key, version)
        WHERE membership.account = ? ORDER BY detail.row_key
    ''', (account,))
    rows = tuple(
        EligibleRow(detail['row_key'], detail['version'], json.loads(detail['reading']), detail['verdict'],
                    detail['meter_version'], detail['meter_origin'], detail['quality_version'],
                    detail['quality_origin'])
        for detail in records
    )
    totals = LedgerTotals(stored['measured_count'], int(stored['liters']))
    return LedgerSnapshot(account, stored['site'], stored['day'], rows, totals)


def load_current(db, site, day):
    reference = db.execute('''
        SELECT account FROM current_ledgers WHERE site = ? AND day = ?
    ''', (site, day)).fetchone()
    if reference is None:
        return LedgerSnapshot(None, site, day, (), LedgerTotals(0, 0))
    return load_snapshot(db, reference['account'])
