from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from freeze.days import freeze_day
from freeze.projection import project_frozen_column
from intake.register import admit
from intake.store import open_warehouse
from issue.register import issue_column, open_issuer
from ledger.publish import publish
from ledger.readback import read
from source.observations import record
from source.pending import open_source


_SAMPLE_DIR = Path(__file__).resolve().parents[1] / 'samples'


def fixture(name):
    return json.loads((_SAMPLE_DIR / f'{name}.json').read_text(encoding='utf-8'))


def visible(view):
    return tuple(
        (row.row, row.version, row.reading, row.verdict,
         row.meter_version, row.meter_origin, row.quality_version, row.quality_origin)
        for row in view.rows
    )


def totals(view):
    return view.totals.measured_count, view.totals.total_liters


class Workflow:
    def __init__(self, directory):
        self.resources = ExitStack()
        self.source_path = directory / 'source.sqlite'
        self.warehouse_path = directory / 'warehouse.sqlite'
        self.directory = directory
        try:
            self.source = self.resources.enter_context(closing(open_source(self.source_path)))
            self.warehouse = self.resources.enter_context(closing(open_warehouse(self.warehouse_path)))
            self.issuers = {}
            for column in ('meter', 'quality'):
                self.issuers[column] = self.resources.enter_context(
                    closing(open_issuer(directory / f'{column}.sqlite', column))
                )
            self.initial_sample = fixture('initial')
            self.issue_numbers = fixture('handover')['issues']
            self.site = self.initial_sample['site']
            self.day = self.initial_sample['day']
        except BaseException:
            self.resources.close()
            raise

    def close(self):
        self.resources.close()

    def add(self, row, column, value, origin, *, correction=None):
        return record(self.source, self.site, self.day, row, column, value, origin, correction=correction)

    def register_initial(self, readings=None):
        entries = []
        for original in self.initial_sample['records']:
            entry = dict(original)
            if readings is not None and entry['column'] == 'meter' and entry['row'] in readings:
                entry['value'] = readings[entry['row']]
            entries.append(self.add(**entry))
        return tuple(entries)

    def freeze(self, entries=None):
        entry_ids = None if entries is None else [entry.entry_id for entry in entries]
        return freeze_day(self.source, self.site, self.day, entry_ids)

    def issue(self, frozen, batch):
        names = self.issue_numbers[batch]
        return {
            column: issue_column(db, frozen.version, column, names[column],
                                 projection=project_frozen_column(self.source, frozen.version, column))
            for column, db in self.issuers.items()
        }

    def open_day(self, readings=None):
        frozen = self.freeze(self.register_initial(readings))
        packets = self.issue(frozen, 'initial')
        for raw in packets.values():
            self.receive(raw)
        return frozen, self.publish().view

    def corrections(self):
        entries = tuple(self.add(**entry) for entry in fixture('corrections')['records'])
        frozen = self.freeze(entries)
        return frozen, self.issue(frozen, 'corrected')

    def receive(self, raw):
        return admit(self.warehouse, raw)

    def publish(self, *, before_commit=None):
        return publish(self.warehouse, self.site, self.day, before_commit=before_commit)

    def read(self, account=None):
        if account is None:
            return read(self.warehouse, site=self.site, day=self.day)
        return read(self.warehouse, account=account)

    def reopen_source(self):
        self.source.close()
        self.source = self.resources.enter_context(closing(open_source(self.source_path)))

    def reopen_warehouse(self):
        self.warehouse.close()
        self.warehouse = self.resources.enter_context(closing(open_warehouse(self.warehouse_path)))

    def disconnect_source(self):
        self.source.close()
        for db in self.issuers.values():
            db.close()
        self.source_path.unlink()


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory(prefix='qingcha-test-')
        self.addCleanup(workspace.cleanup)
        self.flow = Workflow(Path(workspace.name))
        self.addCleanup(self.flow.close)
