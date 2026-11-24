from issue.envelope import ColumnRow
from source.origins import validate_origin
from source.quality import validate_verdict


def build_quality_rows(projection):
    if projection.column != 'quality':
        raise ValueError('expected a quality projection')
    rows = []
    for body in projection.bodies:
        if body.column != 'quality':
            raise ValueError('quality projection contains an unrelated body')
        verdict = validate_verdict(body.value)
        origin = validate_origin(body.origin)
        rows.append(ColumnRow(body.row, verdict, origin.reference))
    return tuple(rows)
