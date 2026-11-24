from collections import defaultdict
from dataclasses import dataclass

from freeze.revisions import RowRevision
from source.origins import require_number
from source.pending import PendingEntry


@dataclass(frozen=True)
class RowChange:
    row: str
    parent: RowRevision | None
    entries: tuple[PendingEntry, ...]


def validate_freeze_scope(pending, entry_ids):
    if entry_ids is None:
        selected = pending
    else:
        by_id = {entry.entry_id: entry for entry in pending}
        ids = tuple(require_number(number, 'registration number') for number in entry_ids)
        if len(ids) != len(set(ids)) or any(number not in by_id for number in ids):
            raise ValueError('freeze scope contains duplicate or unavailable registrations')
        selected = tuple(by_id[number] for number in ids)
    if not selected:
        raise ValueError('freeze scope is empty')
    return selected


def determine_row_changes(entries, parents):
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry.row].append(entry)
    changes = []
    for row, group in sorted(grouped.items()):
        parent = parents.get(row)
        columns = {entry.column for entry in group}
        if len(columns) != len(group):
            raise ValueError(f'freeze scope selects several registrations for {row} in one column')
        if parent is None:
            if columns != {'meter', 'quality'} or any(entry.correction is not None for entry in group):
                raise ValueError(f'first frozen revision of {row} requires its two original columns')
        elif any(entry.correction is None for entry in group):
            raise ValueError(f'only named corrections may advance frozen row {row}')
        changes.append(RowChange(row, parent, tuple(sorted(group, key=lambda entry: entry.column))))
    return tuple(changes)
