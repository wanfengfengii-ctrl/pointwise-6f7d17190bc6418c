from dataclasses import dataclass

from intake.materials import list_day_materials
from intake.store import warehouse_transaction
from ledger.eligibility import PendingRow, evaluate_revisions
from ledger.snapshots import LedgerSnapshot, advance_current, load_current, save_snapshot
from ledger.totals import calculate_totals
from source.origins import validate_station_day


@dataclass(frozen=True)
class PublicationResult:
    view: LedgerSnapshot
    pending: tuple[PendingRow, ...]


def publish(db, site, day, *, before_commit=None):
    validate_station_day(site, day)
    with warehouse_transaction(db):
        current = load_current(db, site, day)
        materials = list_day_materials(db, site, day)
        advances, pending = evaluate_revisions(materials, current.rows)
        if not advances:
            return PublicationResult(current, pending)
        by_row = {row.row: row for row in current.rows}
        by_row.update((row.row, row) for row in advances)
        rows = tuple(by_row[key] for key in sorted(by_row))
        totals = calculate_totals(rows)
        snapshot = save_snapshot(db, site, day, rows, totals, advances)
        advance_current(db, snapshot)
        if before_commit is not None:
            before_commit()
        return PublicationResult(snapshot, pending)
