from dataclasses import dataclass


@dataclass(frozen=True)
class Receipt:
    receipt_id: int
    item_id: str
    site: str
    day: str
    version: int
    column: str
    canonical: str


def find_receipt(db, item_id):
    stored = db.execute('''
        SELECT receipt_id, item_id, site, day, version, column_name, canonical
        FROM receipts WHERE item_id = ?
    ''', (item_id,)).fetchone()
    return Receipt(*stored) if stored is not None else None


def bind_receipt(db, item, canonical):
    cursor = db.execute('''
        INSERT INTO receipts(item_id, site, day, version, column_name, canonical)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (item.item_id, item.site, item.day, item.version, item.column, canonical))
    return Receipt(cursor.lastrowid, item.item_id, item.site, item.day,
                   item.version, item.column, canonical)


def matches_received_item(receipt, canonical):
    return receipt.canonical == canonical
