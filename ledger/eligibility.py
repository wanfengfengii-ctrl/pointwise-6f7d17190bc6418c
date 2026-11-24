from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from intake.materials import ReceivedMaterial


_COLUMNS = ('meter', 'quality')


@dataclass(frozen=True)
class EligibleRow:
    row: str
    version: int
    reading: int | None
    verdict: str
    meter_version: int
    meter_origin: str
    quality_version: int
    quality_origin: str


@dataclass(frozen=True)
class PendingRow:
    row: str
    version: int
    missing: tuple[str, ...]


def _assemble(row, version, evidence, bodies):
    if len(evidence) != 1:
        return None, ('evidence',)
    proof, = evidence
    required = {column: proof.required_version(column) for column in _COLUMNS}
    material = {}
    missing = []
    for column, body_version in required.items():
        if body_version != version and bodies.get((row, version, column)):
            return None, ('evidence',)
        exact = bodies.get((row, body_version, column), ())
        if len(exact) != 1:
            missing.append(column)
        else:
            material[column], = exact
    if missing:
        return None, tuple(missing)
    meter = material['meter']
    quality = material['quality']
    return EligibleRow(row, version, meter.value, quality.value,
                       required['meter'], meter.origin, required['quality'], quality.origin), ()


def evaluate_revisions(materials: Iterable[ReceivedMaterial], current):
    """Evaluate material from one station day against that day's published rows."""
    targets = defaultdict(set)
    proofs = defaultdict(set)
    bodies = defaultdict(set)
    for material in materials:
        item = material.envelope
        for body in item.rows:
            targets[body.row].add(item.version)
            bodies[body.row, item.version, item.column].add(body)
        for proof in item.evidence:
            targets[proof.row].add(item.version)
            proofs[proof.row, item.version].add(proof)

    advances = []
    pending = []
    current_versions = {row.row: row.version for row in current}
    for row, versions in sorted(targets.items()):
        floor = current_versions.get(row, 0)
        for version in sorted(versions, reverse=True):
            if version <= floor:
                break
            assembled, missing = _assemble(row, version, proofs.get((row, version), ()), bodies)
            if assembled is None:
                pending.append(PendingRow(row, version, missing))
            else:
                advances.append(assembled)
                break
    return tuple(advances), tuple(pending)
