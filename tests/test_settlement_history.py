from pathlib import Path

from intake.store import open_warehouse
from issue.register import open_issuer
from ledger.readback import export_settlement
from settlement_fixture import SITE, DAY, build_history
from source.pending import admit_settlement, open_source, read_settlement
from tests.support import WorkflowTest


def _keyed(result):
    return {(entry['correction'], entry['column']): entry
            for entry in result['entries']}


class SettlementHistoryTests(WorkflowTest):
    def _build(self, publish_c2, *, name=None):
        root = Path(self.flow.directory)
        directory = root / (name or f'history-{publish_c2}')
        directory.mkdir(exist_ok=True)
        source = open_source(directory / 'settlement-source.sqlite')
        warehouse = open_warehouse(directory / 'settlement-warehouse.sqlite')
        issuers = {
            column: open_issuer(directory / f'settlement-{column}.sqlite', column)
            for column in ('meter', 'quality')
        }
        self.addCleanup(source.close)
        self.addCleanup(warehouse.close)
        for issuer in issuers.values():
            self.addCleanup(issuer.close)
        build_history(source, warehouse, issuers, publish_c2=publish_c2)
        return source, warehouse

    def _receipts(self, warehouse, accounts):
        return {account: export_settlement(warehouse, account) for account in accounts}

    def test_published_c2_history_marks_both_columns_used_at_account_2(self):
        source, warehouse = self._build(True)
        for raw in self._receipts(warehouse, (1, 2, 3)).values():
            admit_settlement(source, raw)
        result = read_settlement(source, site=SITE, day=DAY)
        self.assertEqual(result['account'], 3)
        entries = _keyed(result)
        self.assertEqual(
            (entries[('C2', 'meter')]['state'], entries[('C2', 'meter')]['decided_at']),
            ('used', 2),
        )
        self.assertEqual(
            (entries[('C2', 'quality')]['state'], entries[('C2', 'quality')]['decided_at']),
            ('used', 2),
        )
        self.assertEqual(
            (entries[('C3', 'meter')]['state'], entries[('C3', 'meter')]['decided_at']),
            ('used', 3),
        )
        self.assertEqual(
            (entries[('C4', 'meter')]['state'], entries[('C4', 'meter')]['decided_at']),
            ('pending', None),
        )
        # 初版两栏在 1 号账已用。
        self.assertEqual(
            (entries[(None, 'meter')]['state'], entries[(None, 'meter')]['decided_at']),
            ('used', 1),
        )
        self.assertEqual(
            (entries[(None, 'quality')]['state'], entries[(None, 'quality')]['decided_at']),
            ('used', 1),
        )

    def test_skipped_c2_history_marks_meter_skipped_and_quality_used(self):
        source, warehouse = self._build(False)
        # 覆盖上游站的 1 号与 3 号即可证明完整前缀，2 号固定账属于下游站。
        receipts = self._receipts(warehouse, (1, 3))
        # 乱序送达：3 号先到。
        admit_settlement(source, receipts[3])
        self.assertIsNone(read_settlement(source, site=SITE, day=DAY)['account'])
        admit_settlement(source, receipts[1])
        result = read_settlement(source, site=SITE, day=DAY)
        self.assertEqual(result['account'], 3)
        entries = _keyed(result)
        self.assertEqual(
            (entries[('C2', 'meter')]['state'], entries[('C2', 'meter')]['decided_at']),
            ('skipped', 3),
        )
        self.assertEqual(
            (entries[('C2', 'quality')]['state'], entries[('C2', 'quality')]['decided_at']),
            ('used', 3),
        )
        self.assertEqual(
            (entries[('C3', 'meter')]['state'], entries[('C3', 'meter')]['decided_at']),
            ('used', 3),
        )
        self.assertEqual(
            (entries[('C4', 'meter')]['state'], entries[('C4', 'meter')]['decided_at']),
            ('pending', None),
        )

    def test_delivery_order_does_not_change_final_settlement(self):
        for index, order in enumerate(((1, 2, 3), (3, 2, 1), (2, 3, 1), (1, 3, 2))):
            with self.subTest(order=order):
                source, warehouse = self._build(True, name=f'order-{index}')
                receipts = self._receipts(warehouse, (1, 2, 3))
                for account in order:
                    admit_settlement(source, receipts[account])
                result = read_settlement(source, site=SITE, day=DAY)
                self.assertEqual(result['account'], 3)
                states = {key: (entry['state'], entry['decided_at'])
                          for key, entry in _keyed(result).items()}
                self.assertEqual(states[('C2', 'meter')], ('used', 2))
                self.assertEqual(states[('C2', 'quality')], ('used', 2))
                self.assertEqual(states[('C3', 'meter')], ('used', 3))
                self.assertEqual(states[('C4', 'meter')], ('pending', None))

    def test_first_decision_account_is_kept_for_earlier_version(self):
        # 本版（v2 读数）先出现记已用；后续更高版（v3）不得改写首次决定。
        source, warehouse = self._build(True)
        for raw in self._receipts(warehouse, (1, 2)).values():
            admit_settlement(source, raw)
        partial = read_settlement(source, site=SITE, day=DAY)
        self.assertEqual(partial['account'], 2)
        c2_meter = _keyed(partial)[('C2', 'meter')]
        self.assertEqual((c2_meter['state'], c2_meter['decided_at']), ('used', 2))
        admit_settlement(source, export_settlement(warehouse, 3))
        settled = _keyed(read_settlement(source, site=SITE, day=DAY))
        self.assertEqual(
            (settled[('C2', 'meter')]['state'], settled[('C2', 'meter')]['decided_at']),
            ('used', 2),
        )

    def test_entries_are_ordered_by_entry_id_and_carry_frozen_version(self):
        source, warehouse = self._build(True)
        for raw in self._receipts(warehouse, (1, 2, 3)).values():
            admit_settlement(source, raw)
        result = read_settlement(source, site=SITE, day=DAY)
        entry_ids = [entry['entry_id'] for entry in result['entries']]
        self.assertEqual(entry_ids, sorted(entry_ids))
        for entry in result['entries']:
            self.assertIn(entry['column'], ('meter', 'quality'))
            self.assertIsInstance(entry['version'], int)

    def test_offline_query_after_warehouse_disconnect(self):
        source, warehouse = self._build(True)
        for raw in self._receipts(warehouse, (1, 2, 3)).values():
            admit_settlement(source, raw)
        base = Path(self.flow.directory) / 'history-True'
        warehouse.close()
        for path in (base / 'settlement-meter.sqlite',
                     base / 'settlement-quality.sqlite',
                     base / 'settlement-warehouse.sqlite'):
            path.unlink()
        # 仅凭本地冻结登记与已送达回执离线结清。
        result = read_settlement(source, site=SITE, day=DAY)
        self.assertEqual(result['account'], 3)
        self.assertEqual(_keyed(result)[('C4', 'meter')]['state'], 'pending')
