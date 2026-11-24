from dataclasses import dataclass

from source.origins import SourceOrigin
from source.pending import correction_day, has_original_row, save_pending
from storage import write_transaction


@dataclass(frozen=True)
class CorrectionEntry:
    site: str
    day: str
    row: str
    column: str
    value: int | str | None
    origin: SourceOrigin
    correction: str


def record_correction(db, entry):
    """File a validated correction while checking its source registration."""
    with write_transaction(db):
        if not has_original_row(db, entry.site, entry.day, entry.row):
            raise ValueError('correction has no original row in this station day')
        bound = correction_day(db, entry.correction)
        if bound is not None and bound != (entry.site, entry.day):
            raise ValueError('correction number belongs to another station day')
        return save_pending(
            db, entry.site, entry.day, entry.row, entry.column,
            entry.value, entry.origin, correction=entry.correction,
        )
