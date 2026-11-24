from dataclasses import dataclass

from freeze.history import load_frozen_bodies, load_frozen_day, load_row_revision
from freeze.revisions import FrozenBody, RowRevision


@dataclass(frozen=True)
class ColumnProjection:
    site: str
    day: str
    version: int
    column: str
    revisions: tuple[RowRevision, ...]
    bodies: tuple[FrozenBody, ...]


def project_frozen_column(db, version, column):
    if column not in ('meter', 'quality'):
        raise ValueError('unknown column')
    frozen = load_frozen_day(db, version)
    revisions = []
    for target in frozen.rows:
        current = target
        revisions.append(current)
        while current.parent is not None:
            current = load_row_revision(db, frozen.site, frozen.day, target.row, current.parent)
            revisions.append(current)
    bodies = load_frozen_bodies(db, version, column)
    return ColumnProjection(frozen.site, frozen.day, version, column, tuple(revisions), bodies)
