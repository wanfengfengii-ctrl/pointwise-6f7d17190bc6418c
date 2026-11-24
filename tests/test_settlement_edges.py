import json

from ledger.readback import export_settlement
from source.observations import record
from source.pending import admit_settlement, read_settlement
from tests.support import WorkflowTest


def _payload(raw, **changes):
    data = json.loads(raw)
    data.update(changes)
    return data


class SettlementEdgeTests(WorkflowTest):
    def _open_with_three_accounts(self):
        self.flow.open_day()
        _, packets = self.flow.corrections()
        self.flow.receive(packets['meter'])
        self.flow.publish()
        self.flow.receive(packets['quality'])
        self.flow.publish()
        return {account: export_settlement(self.flow.warehouse, account)
                for account in (1, 2, 3)}

    def test_forked_predecessor_is_rejected_but_chain_stays_usable(self):
        receipts = self._open_with_three_accounts()
        admit_settlement(self.flow.source, receipts[1])
        # 另一张账也声明挂在 1 号之后（同前件分叉），其行版身份本身合法。
        forged = _payload(receipts[3], account=9, previous_account=1)
        # 先受理真 2 号，前件 1 此时已有后继，伪造的第二后继必须拒收。
        admit_settlement(self.flow.source, receipts[2])
        with self.assertRaisesRegex(ValueError, 'station day predecessor'):
            admit_settlement(self.flow.source, json.dumps(forged, ensure_ascii=False))
        # 真 3 号仍可受理并结清，分叉件未留下任何记录。
        admit_settlement(self.flow.source, receipts[3])
        self.assertEqual(
            read_settlement(self.flow.source, site=self.flow.site, day=self.flow.day)['account'], 3)
        self.assertIsNone(
            self.flow.source.execute(
                'SELECT 1 FROM settlement_receipts WHERE account = 9').fetchone())

    def test_second_station_day_head_is_rejected(self):
        receipts = self._open_with_three_accounts()
        admit_settlement(self.flow.source, receipts[1])
        forged = _payload(receipts[2], account=8, previous_account=None)
        with self.assertRaisesRegex(ValueError, 'opens this station day prefix'):
            admit_settlement(self.flow.source, json.dumps(forged, ensure_ascii=False))

    def test_self_referential_predecessor_is_rejected(self):
        receipts = self._open_with_three_accounts()
        forged = _payload(receipts[1], previous_account=1)
        with self.assertRaisesRegex(ValueError, 'its own predecessor'):
            admit_settlement(self.flow.source, json.dumps(forged, ensure_ascii=False))

    def test_explicit_null_reading_round_trips(self):
        # A 行读数改为明确缺测后出版，回执须保留 null 并通过身份核对。
        self.flow.open_day()
        missing = self.flow.add('A', 'meter', None, 'meter-A-missing', correction='missing-01')
        frozen = self.flow.freeze([missing])
        packet = self.flow.issue(frozen, 'corrected')['meter']
        self.flow.receive(packet)
        self.flow.publish()
        raw = export_settlement(self.flow.warehouse, 2)
        row = next(row for row in json.loads(raw)['rows'] if row['row'] == 'A')
        self.assertIsNone(row['reading'])
        self.assertEqual(admit_settlement(self.flow.source, raw), 2)

    def test_late_frozen_registration_appears_in_later_query(self):
        receipts = self._open_with_three_accounts()
        for raw in receipts.values():
            admit_settlement(self.flow.source, raw)
        before = read_settlement(self.flow.source, site=self.flow.site, day=self.flow.day)
        # 新具名勘误先登记未冻结：查询不包含它。
        late = record(self.flow.source, self.flow.site, self.flow.day, 'A', 'meter',
                      23, 'meter-A-late', correction='C-late')
        same = read_settlement(self.flow.source, site=self.flow.site, day=self.flow.day)
        self.assertEqual(same, before)
        # 冻结后再查询，新改栏出现且尚无出版证据，记未结清。
        self.flow.freeze([late])
        after = read_settlement(self.flow.source, site=self.flow.site, day=self.flow.day)
        entry = next(item for item in after['entries'] if item['correction'] == 'C-late')
        self.assertEqual((entry['state'], entry['decided_at']), ('pending', None))
        self.assertEqual(entry['column'], 'meter')

    def test_query_without_any_receipt_returns_empty_prefix(self):
        self.flow.open_day()
        result = read_settlement(self.flow.source, site=self.flow.site, day=self.flow.day)
        self.assertIsNone(result['account'])
        self.assertTrue(result['entries'])
        self.assertTrue(all(entry['state'] == 'pending' for entry in result['entries']))
        self.assertTrue(all(entry['decided_at'] is None for entry in result['entries']))
        self.assertEqual(
            [entry['entry_id'] for entry in result['entries']],
            sorted(entry['entry_id'] for entry in result['entries']),
        )

    def test_rejected_receipt_leaves_no_rows_behind(self):
        receipts = self._open_with_three_accounts()
        tampered = json.loads(receipts[1])
        tampered['rows'][0]['reading'] = 999
        with self.assertRaises(ValueError):
            admit_settlement(self.flow.source, json.dumps(tampered, ensure_ascii=False))
        counts = self.flow.source.execute(
            'SELECT (SELECT COUNT(*) FROM settlement_receipts), '
            '(SELECT COUNT(*) FROM settlement_receipt_rows)').fetchone()
        self.assertEqual(tuple(counts), (0, 0))
