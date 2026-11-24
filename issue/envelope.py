from dataclasses import asdict, dataclass, replace
import json


@dataclass(frozen=True)
class ColumnRow:
    row: str
    value: int | str | None
    origin: str


@dataclass(frozen=True)
class RevisionEvidence:
    row: str
    parent: int | None
    meter_version: int
    quality_version: int

    def required_version(self, column):
        if column == 'meter':
            return self.meter_version
        if column == 'quality':
            return self.quality_version
        raise ValueError('unknown column')


@dataclass(frozen=True)
class ColumnEnvelope:
    item_id: str
    site: str
    day: str
    version: int
    column: str
    rows: tuple[ColumnRow, ...]
    evidence: tuple[RevisionEvidence, ...]


def encode_envelope(item):
    data = asdict(item)
    data['schema'] = 1
    return json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate JSON field {key}')
        data[key] = value
    return data


def _decode_rows(values, row_type):
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise ValueError('rows and evidence must be arrays of objects')
    return tuple(row_type(**row) for row in values)


def decode_envelope(raw):
    if not isinstance(raw, str):
        raise ValueError('column item must be JSON text')
    data = json.loads(raw, object_pairs_hook=_unique_object)
    fields = {'schema', 'item_id', 'site', 'day', 'version', 'column', 'rows', 'evidence'}
    if not isinstance(data, dict) or data.keys() != fields:
        raise ValueError('invalid column item fields')
    if type(data['schema']) is not int or data.pop('schema') != 1:
        raise ValueError('unsupported column item schema')
    try:
        data['rows'] = _decode_rows(data['rows'], ColumnRow)
        data['evidence'] = _decode_rows(data['evidence'], RevisionEvidence)
        return ColumnEnvelope(**data)
    except TypeError as error:
        raise ValueError('invalid column body or evidence fields') from error


def canonical_item(item):
    ordered = replace(
        item,
        rows=tuple(sorted(item.rows, key=lambda row: row.row)),
        evidence=tuple(sorted(item.evidence, key=lambda row: row.row)),
    )
    return encode_envelope(ordered)
