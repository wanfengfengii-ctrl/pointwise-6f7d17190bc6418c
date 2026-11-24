from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SourceOrigin:
    reference: str


def require_identifier(value, label):
    if not isinstance(value, str) or not value or value != value.strip() or '\x00' in value:
        raise ValueError(f'{label} must be a nonempty trimmed string')
    value.encode('utf-8')
    return value


def validate_origin(value):
    reference = value.reference if isinstance(value, SourceOrigin) else value
    return SourceOrigin(require_identifier(reference, 'origin'))


def validate_station_day(site, day):
    require_identifier(site, 'site')
    require_identifier(day, 'day')
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('day must use YYYY-MM-DD')


def require_number(value, label):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError(f'{label} must be a positive SQLite identifier')
    return value
