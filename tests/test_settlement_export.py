import json

from freeze.days import freeze_day
from freeze.projection import project_frozen_column
from issue.register import issue_column
from ledger.publish import publish
from ledger.readback import export_settlement, read
from ledger.settlement import RECEIPT_SCHEMA
from source.observations import record
from tests.support import WorkflowTest


class SettlementExportTests(WorkflowTest):
    def _publish_three(self):
        first, opened = self.flow.open_day()
        next_day, packets = self.flow.corrections()
        self.flow.receive(packets['meter'])
        middle = self.flow.publish().view
        self.flow.receive(packets['quality'])
        final = self.flow.publish().view
        return opened, middle, final

    def test_export_shape_and_deterministic_re_fetch(self):
        opened, middle, _ = self._publish_three()
        raw = export_settlement(self.flow.warehouse, middle.account)
        again = export_settlement(self.flow.warehouse, middle.account)
        self.assertEqual(raw, again)
        payload = json.loads(raw)
        self.assertEqual(set(payload), {
            'schema', 'account', 'site', 'day', 'previous_account', 'rows',
        })
        self.assertEqual(payload['schema'], RECEIPT_SCHEMA)
        self.assertEqual((payload['account'], payload['site'], payload['day']),
                         (middle.account, middle.site, middle.day))
        self.assertEqual(payload['previous_account'], opened.account)
        self.assertEqual(len(payload['rows']), len(middle.rows))
        by_row = {row['row']: row for row in payload['rows']}
        self.assertEqual(set(by_row), {row.row for row in middle.rows})
        for row in payload['rows']:
            self.assertEqual(set(row), {
                'row', 'version', 'meter_version', 'quality_version',
                'reading', 'verdict', 'meter_origin', 'quality_origin',
            })
        published = next(row for row in middle.rows if row.row == 'B')
        row = by_row['B']
        self.assertEqual((row['row'], row['version'], row['reading'], row['verdict']),
                         (published.row, published.version, published.reading, published.verdict))
        self.assertEqual((row['meter_version'], row['quality_version']),
                         (published.meter_version, published.quality_version))
        # JSON 键序与空白不影响内容，重取文本本身逐字节稳定。
        self.assertEqual(json.loads('  ' + raw + '\n'), payload)

    def test_first_account_has_null_predecessor(self):
        self._publish_three()
        first = json.loads(export_settlement(self.flow.warehouse, 1))
        self.assertIsNone(first['previous_account'])
        second = json.loads(export_settlement(self.flow.warehouse, 2))
        self.assertEqual(second['previous_account'], 1)
        third = json.loads(export_settlement(self.flow.warehouse, 3))
        self.assertEqual(third['previous_account'], 2)

    def test_unknown_account_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            export_settlement(self.flow.warehouse, 404)

    def test_rolled_back_ledger_has_no_receipt_until_retry(self):
        self.flow.open_day()
        _, packets = self.flow.corrections()
        self.flow.receive(packets['meter'])
        warehouse = self.flow.warehouse
        warehouse.execute('CREATE TEMP TABLE audit_permission(code INTEGER PRIMARY KEY)')
        warehouse.execute('''
            CREATE TEMP TABLE audit_attempt (
                code INTEGER REFERENCES audit_permission(code) DEFERRABLE INITIALLY DEFERRED
            )
        ''')

        def fail_commit():
            warehouse.execute('INSERT INTO audit_attempt VALUES (1)')

        with self.assertRaises(Exception):
            self.flow.publish(before_commit=fail_commit)
        with self.assertRaises(LookupError):
            export_settlement(warehouse, 2)
        retried = self.flow.publish().view
        receipt = json.loads(export_settlement(warehouse, retried.account))
        self.assertEqual(receipt['account'], retried.account)
        self.assertEqual(receipt['previous_account'], 1)
        self.assertEqual(export_settlement(warehouse, retried.account),
                         export_settlement(warehouse, retried.account))

    def test_export_works_after_reopening_warehouse(self):
        _, _, final = self._publish_three()
        raw = export_settlement(self.flow.warehouse, final.account)
        self.flow.reopen_warehouse()
        self.assertEqual(export_settlement(self.flow.warehouse, final.account), raw)

    def test_other_station_day_does_not_enter_predecessor_chain(self):
        flow = self.flow
        flow.open_day()
        other_day = '2026-11-19'
        entries = (
            record(flow.source, flow.site, other_day, 'A', 'meter', 5, 'other-meter'),
            record(flow.source, flow.site, other_day, 'A', 'quality', '合格', 'other-quality'),
        )
        frozen = freeze_day(flow.source, flow.site, other_day, [entry.entry_id for entry in entries])
        for column in ('meter', 'quality'):
            raw = issue_column(flow.issuers[column], frozen.version, column,
                               f'other-{column}',
                               projection=project_frozen_column(flow.source, frozen.version, column))
            flow.receive(raw)
        published = publish(flow.warehouse, flow.site, other_day).view
        payload = json.loads(export_settlement(flow.warehouse, published.account))
        self.assertIsNone(payload['previous_account'])
        self.assertEqual(payload['day'], other_day)
        # 固定账读回与回执并存。
        self.assertEqual(read(flow.warehouse, account=published.account).account, published.account)
