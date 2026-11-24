import json

from ledger.readback import export_settlement
from source.pending import admit_settlement, read_settlement
from tests.support import WorkflowTest


def _mutate(raw, **changes):
    payload = json.loads(raw)
    payload.update(changes)
    return json.dumps(payload, ensure_ascii=False)


def _mutate_row(raw, **changes):
    payload = json.loads(raw)
    payload['rows'] = [dict(payload['rows'][0], **changes)]
    return json.dumps(payload, ensure_ascii=False)


class SettlementAdmitTests(WorkflowTest):
    def _three_receipts(self):
        self.flow.open_day()
        _, packets = self.flow.corrections()
        self.flow.receive(packets['meter'])
        self.flow.publish()
        self.flow.receive(packets['quality'])
        self.flow.publish()
        return {account: export_settlement(self.flow.warehouse, account)
                for account in (1, 2, 3)}

    def _admit_all(self, receipts):
        for raw in receipts.values():
            admit_settlement(self.flow.source, raw)

    def test_admit_returns_account_and_query_uses_frozen_versions(self):
        receipts = self._three_receipts()
        self.assertEqual(admit_settlement(self.flow.source, receipts[1]), 1)
        result = read_settlement(self.flow.source,
                                 site=self.flow.site, day=self.flow.day)
        self.assertEqual(result['account'], 1)
        fields = {(entry['row'], entry['column']): entry
                  for entry in result['entries']}
        self.assertEqual(set(fields), {('A', 'meter'), ('A', 'quality'),
                                       ('B', 'meter'), ('B', 'quality')})
        for entry in result['entries']:
            self.assertEqual(set(entry),
                             {'entry_id', 'correction', 'row', 'column',
                              'version', 'state', 'decided_at'})

    def test_replay_is_idempotent_ignoring_whitespace_and_key_order(self):
        receipts = self._three_receipts()
        first = admit_settlement(self.flow.source, receipts[2])
        reordered = json.dumps(json.loads(receipts[2]), sort_keys=False, indent=2)
        self.assertEqual(admit_settlement(self.flow.source, ' \n' + reordered), first)
        self._admit_all(receipts)
        self.assertEqual(admit_settlement(self.flow.source, receipts[2]), first)

    def test_malformed_receipts_are_rejected_without_state_change(self):
        receipts = self._three_receipts()
        bad_inputs = (
            'not json',
            123,
            _mutate(receipts[1], schema=1),
            _mutate(receipts[1], extra=True),
            _mutate(receipts[1], account='1'),
            _mutate(receipts[1], previous_account=1),
            _mutate_row(receipts[1], version=0),
            _mutate_row(receipts[1], meter_version=9),
            _mutate_row(receipts[1], reading=7.5),
            _mutate_row(receipts[1], verdict='未知'),
            _mutate(receipts[1], rows=[]),
            json.dumps({**json.loads(receipts[1]), 'rows': [
                json.loads(receipts[1])['rows'][0],
                json.loads(receipts[1])['rows'][0],
            ]}, ensure_ascii=False),
        )
        for bad in bad_inputs:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    admit_settlement(self.flow.source, bad)
        # 所有坏件均未受理：没有可证前缀，也没有任何受理回执。
        self.assertIsNone(
            read_settlement(self.flow.source, site=self.flow.site, day=self.flow.day)['account'])
        self.assertEqual(
            self.flow.source.execute('SELECT COUNT(*) FROM settlement_receipts').fetchone()[0], 0)
        # 合法回执仍可正常受理。
        self.assertEqual(admit_settlement(self.flow.source, receipts[1]), 1)

    def test_same_account_with_conflicting_content_is_rejected(self):
        receipts = self._three_receipts()
        admit_settlement(self.flow.source, receipts[1])
        conflicting = _mutate_row(receipts[1], meter_origin='tampered-origin')
        with self.assertRaises(ValueError):
            admit_settlement(self.flow.source, conflicting)
        # 原受理内容仍可重投，状态不变。
        self.assertEqual(admit_settlement(self.flow.source, receipts[1]), 1)

    def test_duplicate_station_day_head_is_rejected(self):
        receipts = self._three_receipts()
        admit_settlement(self.flow.source, receipts[1])
        forged = _mutate(receipts[3], previous_account=None)
        with self.assertRaisesRegex(ValueError, 'opens this station day prefix'):
            admit_settlement(self.flow.source, forged)

    def test_predecessor_identity_mismatch_is_rejected(self):
        receipts = self._three_receipts()
        admit_settlement(self.flow.source, receipts[1])
        wrong_day = _mutate(receipts[2], day='2026-11-19')
        with self.assertRaises((ValueError, LookupError)):
            admit_settlement(self.flow.source, wrong_day)
        wrong_site = _mutate(receipts[2], site='别的站')
        with self.assertRaises((ValueError, LookupError)):
            admit_settlement(self.flow.source, wrong_site)
        # 正确身份的回执仍可受理。
        self.assertEqual(admit_settlement(self.flow.source, receipts[2]), 2)

    def test_row_version_unknown_to_local_freeze_is_rejected(self):
        receipts = self._three_receipts()
        forged = _mutate_row(receipts[1], version=99, meter_version=99, quality_version=99)
        with self.assertRaises((ValueError, LookupError)):
            admit_settlement(self.flow.source, forged)

    def test_column_body_identity_mismatch_is_rejected(self):
        receipts = self._three_receipts()
        # 行版 2（B 行）的读数栏正文冻结于版次 2，伪称沿用版次 1。
        payload = json.loads(receipts[2])
        target = next(row for row in payload['rows'] if row['row'] == 'B')
        target['meter_version'] = 1
        forged = json.dumps(payload, ensure_ascii=False)
        with self.assertRaises(ValueError):
            admit_settlement(self.flow.source, forged)
        # 正文内容或出处被篡改同样拒收。
        for tampered in (
            _mutate_row(receipts[1], reading=99),
            _mutate_row(receipts[1], quality_origin='Q-other'),
        ):
            with self.subTest(tampered=tampered):
                with self.assertRaisesRegex(ValueError, 'frozen registration'):
                    admit_settlement(self.flow.source, tampered)

    def test_missing_predecessor_is_kept_and_completes_later(self):
        receipts = self._three_receipts()
        self.assertEqual(admit_settlement(self.flow.source, receipts[3]), 3)
        pending = read_settlement(self.flow.source,
                                  site=self.flow.site, day=self.flow.day)
        self.assertIsNone(pending['account'])
        self.assertTrue(all(entry['state'] == 'pending' for entry in pending['entries']))
        admit_settlement(self.flow.source, receipts[1])
        self.assertEqual(read_settlement(
            self.flow.source, site=self.flow.site, day=self.flow.day)['account'], 1)
        admit_settlement(self.flow.source, receipts[2])
        completed = read_settlement(self.flow.source,
                                    site=self.flow.site, day=self.flow.day)
        self.assertEqual(completed['account'], 3)

    def test_reopen_source_keeps_admissions(self):
        receipts = self._three_receipts()
        self._admit_all(receipts)
        before = read_settlement(self.flow.source,
                                 site=self.flow.site, day=self.flow.day)
        self.flow.reopen_source()
        after = read_settlement(self.flow.source,
                                site=self.flow.site, day=self.flow.day)
        self.assertEqual(after, before)
        self.assertEqual(admit_settlement(self.flow.source, receipts[3]), 3)

    def test_unrelated_station_day_receipt_is_rejected(self):
        self._three_receipts()
        foreign = json.dumps({
            'schema': 2, 'account': 7, 'site': self.flow.site,
            'day': '2026-11-19', 'previous_account': None,
            'rows': [{
                'row': 'A', 'version': 1, 'meter_version': 1, 'quality_version': 1,
                'reading': 7, 'verdict': '合格',
                'meter_origin': 'meter-A-open', 'quality_origin': 'quality-A-open',
            }],
        }, ensure_ascii=False)
        with self.assertRaises((ValueError, LookupError)):
            admit_settlement(self.flow.source, foreign)
