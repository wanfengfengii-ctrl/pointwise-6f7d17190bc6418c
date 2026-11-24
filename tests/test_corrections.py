from contextlib import closing
import json
import sqlite3

from freeze.history import load_frozen_day, load_row_revision
from freeze.projection import project_frozen_column
from intake.materials import load_original
from intake.store import open_warehouse
from issue.envelope import decode_envelope
from issue.register import issue_column, load_original_issue, open_issuer
from ledger.readback import read
from tests.support import WorkflowTest, totals, visible


class CorrectionTests(WorkflowTest):
    def test_readings_first(self):
        flow = self.flow
        start, opened = flow.open_day()
        next_day, packets = flow.corrections()
        flow.receive(packets['meter'])
        flow.disconnect_source()
        partial = flow.publish()
        old, new = start.version, next_day.version
        expected_rows = (
            ('A', old, 7, '合格', old, 'meter-A-open', old, 'quality-A-open'),
            ('B', new, 17, '合格', new, 'meter-B-amend', old, 'quality-B-open'),
        )
        actual = (visible(partial.view), totals(partial.view),
                  [(row.row, row.version, row.missing) for row in partial.pending])
        expected = (expected_rows, (2, 24), [('A', new, ('quality',))])
        self.assertEqual(actual, expected, 'ROW_REVISION_MISMATCH')
        self.assertEqual([(row.row, row.parent, row.changed) for row in next_day.rows], [
            ('A', old, ('meter', 'quality')), ('B', old, ('meter',)),
        ])
        evidence = decode_envelope(packets['meter']).evidence
        self.assertEqual([(row.row, row.meter_version, row.quality_version) for row in evidence],
                         [('A', new, new), ('B', new, old)])
        self.assertEqual(flow.read(), partial.view)
        flow.receive(packets['quality'])
        complete = flow.publish()
        self.assertEqual(complete.pending, ())
        self.assertEqual(totals(complete.view), (2, 30))
        self.assertEqual(visible(complete.view), (
            ('A', new, 13, '待复核', new, 'meter-A-amend', new, 'quality-A-amend'), expected_rows[1],
        ))
        self.assertEqual(totals(flow.read(opened.account)), (2, 18))
        self.assertEqual(flow.read(partial.view.account), partial.view)
        scenario = {
            'case_id': 'v3',
            'input': {'opened': [7, 11], 'received_meter': [13, 17],
                      'quality_change_required': ['A'], 'quality_packet_in_transit': True},
            'expected': [2, 24],
            'actual': list(totals(partial.view)),
            'downstream_expected': {'fixed_initial_liters': 18, 'complete_liters': 30},
            'downstream_actual': {'fixed_initial_liters': flow.read(opened.account).totals.total_liters,
                                  'complete_liters': flow.read().totals.total_liters},
            'code_paths': ['issue/register.py', 'intake/materials.py', 'ledger/eligibility.py',
                           'ledger/publish.py', 'ledger/readback.py'],
            'oracle': 'A 质控仍在途而留在 7 L，B 沿用旧质控后为 17 L，24=7+17。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, sort_keys=True))

    def test_quality_first_frozen_parent_and_original_reissue(self):
        flow = self.flow
        frozen, opened = flow.open_day()
        next_day, packets = flow.corrections()
        stored = load_frozen_day(flow.source, frozen.version)
        parent = load_row_revision(flow.source, flow.site, flow.day, 'A', frozen.version)
        self.assertEqual(stored, frozen)
        self.assertIsNone(parent.parent)
        self.assertEqual(next_day.rows[0].parent, parent.version)
        for column, value, origin in [('meter', 7, 'meter-A-open'), ('quality', '合格', 'quality-A-open')]:
            projection = project_frozen_column(flow.source, frozen.version, column)
            body = next(body for body in projection.bodies if body.row == 'A')
            self.assertEqual((body.value, body.origin), (value, origin))
        with closing(open_issuer(flow.directory / 'quality.sqlite', 'quality')) as issuer:
            again = issue_column(issuer, next_day.version, 'quality', 'Q-02')
            self.assertEqual(again, packets['quality'])
            self.assertEqual(load_original_issue(issuer, 'Q-02'), again)
            with self.assertRaises(ValueError):
                issue_column(issuer, frozen.version, 'quality', 'Q-02')
            with self.assertRaises(ValueError):
                issue_column(issuer, next_day.version, 'meter', 'M-WRONG')
        item = decode_envelope(again)
        self.assertEqual(item.version, next_day.version)
        self.assertEqual([(row.row, row.value, row.origin) for row in item.rows],
                         [('A', '待复核', 'quality-A-amend')])
        quality_receipt = flow.receive(again)
        waiting = flow.publish()
        self.assertEqual(waiting.view, opened)
        self.assertEqual([(row.row, row.missing) for row in waiting.pending],
                         [('A', ('meter',)), ('B', ('meter',))])
        flow.receive(packets['meter'])
        completed = flow.publish().view
        self.assertEqual(totals(completed), (2, 30))
        self.assertEqual([(row.row, row.reading, row.verdict) for row in completed.rows],
                         [('A', 13, '待复核'), ('B', 17, '合格')])
        flow.reopen_warehouse()
        self.assertEqual(flow.receive(again), quality_receipt)
        self.assertEqual(flow.publish().view, completed)

    def test_explicit_missing_correction(self):
        flow = self.flow
        first, _ = flow.open_day()
        edited = flow.add('A', 'meter', None, 'meter-A-missing', correction='missing-01')
        frozen = flow.freeze([edited])
        packet = flow.issue(frozen, 'corrected')['meter']
        flow.receive(packet)
        result = flow.publish()
        self.assertEqual(result.pending, ())
        self.assertEqual(totals(result.view), (1, 11))
        self.assertEqual(visible(flow.read())[0],
                         ('A', frozen.version, None, '合格', frozen.version,
                          'meter-A-missing', first.version, 'quality-A-open'))

    def test_inherited_exact_body_can_arrive_after_newer_meter(self):
        flow = self.flow
        initial, opened = flow.open_day()
        quality = flow.add('A', 'quality', '合格', 'quality-A-open', correction='check-A-again')
        parent = flow.freeze([quality])
        parent_item = flow.issue(parent, 'corrected')['quality']
        meter = flow.add('A', 'meter', 19, 'meter-A-later', correction='read-A-again')
        target = flow.freeze([meter])
        projection = project_frozen_column(flow.source, target.version, 'meter')
        new_item = issue_column(flow.issuers['meter'], target.version, 'meter', 'M-03', projection=projection)
        flow.receive(new_item)
        waiting = flow.publish()
        self.assertEqual(waiting.view, opened)
        self.assertIn(('A', target.version, ('quality',)),
                      [(row.row, row.version, row.missing) for row in waiting.pending])
        flow.receive(parent_item)
        result = flow.publish()
        self.assertEqual(result.pending, ())
        row = result.view.rows[0]
        self.assertGreater(parent.version, initial.version)
        self.assertEqual((row.version, row.quality_version, row.quality_origin),
                         (target.version, parent.version, 'quality-A-open'))
        self.assertEqual(totals(result.view), (2, 30))

    def test_failed_commit_preserves_and_retries_snapshot(self):
        flow = self.flow
        _, opened = flow.open_day()
        _, packets = flow.corrections()
        flow.receive(packets['meter'])
        middle = flow.publish().view
        self.assertEqual(totals(middle), (2, 24))
        late_receipt = flow.receive(packets['quality'])
        warehouse = flow.warehouse
        warehouse.execute('CREATE TEMP TABLE audit_permission(code INTEGER PRIMARY KEY)')
        warehouse.execute('''
            CREATE TEMP TABLE audit_attempt (
                code INTEGER REFERENCES audit_permission(code) DEFERRABLE INITIALLY DEFERRED
            )
        ''')

        def fail_commit():
            warehouse.execute('INSERT INTO audit_attempt VALUES (1)')

        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            flow.publish(before_commit=fail_commit)
        self.assertFalse(warehouse.in_transaction)
        with closing(open_warehouse(flow.warehouse_path)) as verifier:
            recovered = read(verifier, site=flow.site, day=flow.day)
            self.assertEqual(recovered, middle)
            self.assertEqual(read(verifier, account=opened.account), opened)
            self.assertEqual(read(verifier, account=middle.account), middle)
            with self.assertRaises(LookupError):
                read(verifier, account=middle.account + 1)
            self.assertEqual(load_original(verifier, late_receipt), packets['quality'])
        retried = flow.publish().view
        self.assertEqual(retried.account, middle.account + 1)
        self.assertEqual(totals(retried), (2, 30))
        self.assertEqual(flow.read(middle.account), middle)
        self.assertEqual(flow.read(), retried)
