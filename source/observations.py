import sqlite3

from source.corrections import CorrectionEntry, record_correction
from source.origins import require_identifier, validate_origin, validate_station_day
from source.pending import save_pending
from source.quality import validate_verdict
from storage import write_transaction


def validate_reading(value):
    if value is not None and type(value) is not int:
        raise ValueError('meter must be integer liters or explicit null')
    return value


def record(db, site, day, row, column, value, origin, *, correction=None):
    validate_station_day(site, day)
    require_identifier(row, 'row')
    if column == 'meter':
        value = validate_reading(value)
    elif column == 'quality':
        value = validate_verdict(value)
    else:
        raise ValueError('unknown column')
    origin = validate_origin(origin)
    if correction is not None:
        require_identifier(correction, 'correction')
    try:
        if correction is not None:
            entry = CorrectionEntry(site, day, row, column, value, origin, correction)
            return record_correction(db, entry)
        with write_transaction(db):
            return save_pending(db, site, day, row, column, value, origin)
    except sqlite3.IntegrityError as error:
        raise ValueError('registration already exists for that row and column') from error
