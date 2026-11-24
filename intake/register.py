from intake.materials import store_original
from intake.receipts import bind_receipt, find_receipt, matches_received_item
from intake.store import warehouse_transaction
from intake.validate import validate_column


def admit(db, raw):
    incoming = validate_column(raw)
    item = incoming.envelope
    with warehouse_transaction(db):
        receipt = find_receipt(db, item.item_id)
        if receipt is not None:
            if not matches_received_item(receipt, incoming.canonical):
                raise ValueError(f'item number {item.item_id} is already bound to different content')
            return receipt.receipt_id
        receipt = bind_receipt(db, item, incoming.canonical)
        store_original(db, receipt.receipt_id, raw)
        return receipt.receipt_id
