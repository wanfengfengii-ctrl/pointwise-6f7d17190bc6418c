from issue.envelope import ColumnRow
from source.observations import validate_reading
from source.origins import validate_origin


def build_meter_rows(projection):
    if projection.column != 'meter':
        raise ValueError('expected a meter projection')
    rows = []
    for body in projection.bodies:
        if body.column != 'meter':
            raise ValueError('meter projection contains an unrelated body')
        value = validate_reading(body.value)
        origin = validate_origin(body.origin)
        rows.append(ColumnRow(body.row, value, origin.reference))
    return tuple(rows)
