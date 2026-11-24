from dataclasses import dataclass


@dataclass(frozen=True)
class LedgerTotals:
    measured_count: int
    total_liters: int


def calculate_totals(rows):
    count = 0
    liters = 0
    for row in rows:
        if row.reading is not None:
            count += 1
            liters += row.reading
    return LedgerTotals(count, liters)
