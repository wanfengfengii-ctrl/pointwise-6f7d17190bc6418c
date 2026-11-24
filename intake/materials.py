from dataclasses import dataclass

from issue.envelope import ColumnEnvelope, decode_envelope
from source.origins import require_number


@dataclass(frozen=True)
class ReceivedMaterial:
    receipt_id: int
    envelope: ColumnEnvelope


def store_original(db, receipt_id, raw):
    db.execute('INSERT INTO original_material(receipt_id, raw) VALUES (?, ?)', (receipt_id, raw))


def load_original(db, receipt_id):
    require_number(receipt_id, 'receipt number')
    stored = db.execute('SELECT raw FROM original_material WHERE receipt_id = ?', (receipt_id,)).fetchone()
    if stored is None:
        raise LookupError(f'unknown receipt {receipt_id}')
    return stored['raw']


def list_day_materials(db, site, day):
    records = db.execute('''
        SELECT receipt.receipt_id, material.raw
        FROM receipts AS receipt JOIN original_material AS material USING (receipt_id)
        WHERE receipt.site = ? AND receipt.day = ?
        ORDER BY receipt.receipt_id
    ''', (site, day))
    return tuple(ReceivedMaterial(row['receipt_id'], decode_envelope(row['raw'])) for row in records)
