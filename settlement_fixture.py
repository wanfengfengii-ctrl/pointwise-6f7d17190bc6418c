# Build two publication histories through the original station-day workflow.
import argparse
from contextlib import ExitStack, closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from freeze.days import freeze_day
from freeze.projection import project_frozen_column
from intake.register import admit
from intake.store import open_warehouse
from issue.register import issue_column, open_issuer
from ledger.publish import publish
from ledger.readback import read
from source.observations import record
from source.pending import open_source

SITE = "上游站"
OTHER_SITE = "下游站"
DAY = "2026-11-18"


def build_history(source, warehouse, issuers, *, publish_c2):
    def register(site, readings, correction=None):
        for column, value, origin in readings:
            record(source, site, DAY, "A", column, value, origin, correction=correction)
        return freeze_day(source, site, DAY).version

    def deliver(version, *columns):
        for column in columns:
            projection = project_frozen_column(source, version, column)
            raw = issue_column(issuers[column], version, column, f"{version}-{column}",
                               projection=projection)
            admit(warehouse, raw)

    initial = register(SITE, [("meter", 7, "M1"), ("quality", "合格", "Q1")])
    c2 = register(SITE, [("meter", 13, "M2"), ("quality", "待复核", "Q2")], "C2")
    c3 = register(SITE, [("meter", 19, "M3")], "C3")
    register(SITE, [("meter", 19, "M3")], "C4")
    other = register(OTHER_SITE, [("meter", 1, "MY"), ("quality", "合格", "QY")])

    deliver(initial, "meter", "quality")
    first = publish(warehouse, SITE, DAY).view
    deliver(c2, "quality")
    if publish_c2:
        deliver(c2, "meter")
        second = publish(warehouse, SITE, DAY).view
    else:
        deliver(other, "meter", "quality")
        second = publish(warehouse, OTHER_SITE, DAY).view
    deliver(c3, "meter")
    third = publish(warehouse, SITE, DAY).view
    return tuple(read(warehouse, account=view.account) for view in (first, second, third))


def run_history(directory, publish_c2):
    def database(name):
        return ":memory:" if directory is None else directory / f"{name}.sqlite"

    with ExitStack() as stack:
        source = stack.enter_context(closing(open_source(database("source"))))
        warehouse = stack.enter_context(closing(open_warehouse(database("warehouse"))))
        issuers = {name: stack.enter_context(closing(open_issuer(database(name), name)))
                   for name in ("meter", "quality")}
        return build_history(source, warehouse, issuers, publish_c2=publish_c2)


def verify_inputs(used, skipped):
    for history, middle in ((used, (SITE, 13)), (skipped, (OTHER_SITE, 1))):
        actual = [(book.account, book.site, book.totals.total_liters) for book in history]
        expected = [(1, SITE, 7), (2, *middle), (3, SITE, 19)]
        if actual != expected:
            raise AssertionError((actual, expected))
    if used[-1] != skipped[-1]:
        raise AssertionError("the two final books must be identical")
    row, = used[-1].rows
    actual = (row.row, row.version, row.reading, row.verdict,
              row.meter_version, row.meter_origin, row.quality_version, row.quality_origin)
    expected = ("A", 3, 19, "待复核", 3, "M3", 2, "Q2")
    if actual != expected:
        raise AssertionError((actual, expected))


def main():
    parser = argparse.ArgumentParser(description="Verify the original publication histories")
    parser.add_argument("--memory", action="store_true", help="use in-memory SQLite databases")
    options = parser.parse_args()
    with ExitStack() as stack:
        histories = []
        for publish_c2 in (True, False):
            directory = None
            if not options.memory:
                directory = Path(stack.enter_context(TemporaryDirectory(prefix="qingcha-settlement-")))
            histories.append(run_history(directory, publish_c2))
    verify_inputs(*histories)
    print(json.dumps({key: [asdict(book) for book in history]
                      for key, history in zip(("published_c2", "skipped_c2"), histories)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
