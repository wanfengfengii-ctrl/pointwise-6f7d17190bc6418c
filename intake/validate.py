from dataclasses import dataclass

from issue.envelope import ColumnEnvelope, canonical_item, decode_envelope
from source.origins import require_identifier, require_number, validate_origin, validate_station_day
from source.quality import validate_verdict


@dataclass(frozen=True)
class ValidatedColumn:
    envelope: ColumnEnvelope
    canonical: str


def _validate_bodies(item):
    rows = set()
    for body in item.rows:
        require_identifier(body.row, 'body row')
        validate_origin(body.origin)
        if body.row in rows:
            raise ValueError(f'duplicate body for {body.row}')
        rows.add(body.row)
        if item.column == 'meter':
            if body.value is not None and type(body.value) is not int:
                raise ValueError('meter body must be integer liters or explicit null')
        else:
            validate_verdict(body.value)
    return rows


def _validate_evidence(item, body_rows):
    seen = set()
    for proof in item.evidence:
        require_identifier(proof.row, 'evidence row')
        if proof.row in seen:
            raise ValueError(f'duplicate evidence for {proof.row}')
        seen.add(proof.row)
        required = (
            require_number(proof.meter_version, 'required meter version'),
            require_number(proof.quality_version, 'required quality version'),
        )
        if max(required) != item.version:
            raise ValueError('evidence must bind at least one body from the target version')
        if proof.parent is None:
            if min(required) != item.version:
                raise ValueError('an initial row cannot inherit a body')
        else:
            require_number(proof.parent, 'parent version')
            if proof.parent >= item.version or any(
                proof.parent < version < item.version for version in required
            ):
                raise ValueError('invalid parent or inherited body version')
        if proof.row in body_rows and proof.required_version(item.column) != item.version:
            raise ValueError('own body contradicts the inherited column evidence')


def validate_column(raw):
    item = decode_envelope(raw)
    require_identifier(item.item_id, 'item number')
    validate_station_day(item.site, item.day)
    require_number(item.version, 'day version')
    if item.column not in ('meter', 'quality'):
        raise ValueError('unknown received column')
    body_rows = _validate_bodies(item)
    _validate_evidence(item, body_rows)
    return ValidatedColumn(item, canonical_item(item))
