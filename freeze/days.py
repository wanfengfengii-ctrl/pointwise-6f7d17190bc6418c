from freeze.changes import determine_row_changes, validate_freeze_scope
from freeze.history import find_parent_revisions
from freeze.revisions import FrozenDay, append_frozen_day, append_row_revision
from source.origins import validate_station_day
from source.pending import list_pending, mark_frozen
from storage import write_transaction


def freeze_day(db, site, day, entry_ids=None):
    validate_station_day(site, day)
    with write_transaction(db):
        entries = validate_freeze_scope(list_pending(db, site, day), entry_ids)
        parents = find_parent_revisions(db, site, day, sorted({entry.row for entry in entries}))
        changes = determine_row_changes(entries, parents)
        version = append_frozen_day(db, site, day)
        rows = tuple(
            append_row_revision(db, site, day, version, change.row,
                                change.parent.version if change.parent else None, change.entries)
            for change in changes
        )
        mark_frozen(db, version, entries)
        return FrozenDay(site, day, version, rows)
