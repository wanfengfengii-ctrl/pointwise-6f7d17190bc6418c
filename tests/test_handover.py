from dataclasses import replace

from intake.materials import list_day_materials, load_original
from intake.receipts import find_receipt
from issue.envelope import decode_envelope, encode_envelope
from tests.support import WorkflowTest, totals, visible


class HandoverTests(WorkflowTest):
    def test_replay_and_changed_evidence(self):
        flow = self.flow
        flow.open_day()
        _, packets = flow.corrections()
        original = packets['meter']
        item = decode_envelope(original)
        receipt_id = flow.receive(original)
        reordered = replace(item, rows=item.rows[::-1], evidence=item.evidence[::-1])
        self.assertEqual(flow.receive('  ' + encode_envelope(reordered) + '\n'), receipt_id)
        changed_body = replace(item, rows=(replace(item.rows[0], value=99),) + item.rows[1:])
        changed_evidence = replace(item, evidence=tuple(proof for proof in item.evidence if proof.row != 'B'))
        for altered in (changed_body, changed_evidence):
            with self.subTest(altered=altered), self.assertRaisesRegex(ValueError, 'different content'):
                flow.receive(encode_envelope(altered))
        stored = find_receipt(flow.warehouse, item.item_id)
        self.assertEqual((stored.receipt_id, stored.version), (receipt_id, item.version))
        self.assertEqual(load_original(flow.warehouse, receipt_id), original)
        flow.reopen_warehouse()
        self.assertEqual(flow.receive(original), receipt_id)
        self.assertEqual(load_original(flow.warehouse, receipt_id), original)

    def test_bad_item_has_no_receipt_binding(self):
        flow = self.flow
        frozen = flow.freeze(flow.register_initial())
        original = flow.issue(frozen, 'initial')['meter']
        item = decode_envelope(original)
        bad_boolean = replace(item, rows=(replace(item.rows[0], value=True),) + item.rows[1:])
        bad_duplicate = replace(item, evidence=item.evidence + item.evidence[:1])
        bad_inheritance = replace(item, evidence=(replace(item.evidence[0], quality_version=0),) + item.evidence[1:])
        for bad in (bad_boolean, bad_duplicate, bad_inheritance):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                flow.receive(encode_envelope(bad))
        self.assertIsNone(find_receipt(flow.warehouse, item.item_id))
        self.assertEqual(flow.receive(original), 1)

    def test_stale_receipts_do_not_move_current_or_fixed_snapshot(self):
        flow = self.flow
        first, opened = flow.open_day()
        next_day, packets = flow.corrections()
        old_unreceived = flow.issue(first, 'late')
        flow.receive(packets['meter'])
        flow.receive(packets['quality'])
        latest = flow.publish().view
        self.assertEqual(totals(latest), (2, 30))
        self.assertTrue(all(row.version == next_day.version for row in latest.rows))
        flow.reopen_warehouse()
        for column, raw in old_unreceived.items():
            item = decode_envelope(raw)
            self.assertIsNone(find_receipt(flow.warehouse, item.item_id))
            received = flow.receive(raw)
            self.assertEqual(load_original(flow.warehouse, received), old_unreceived[column])
        self.assertEqual(flow.publish().view, latest)
        self.assertEqual(flow.read(), latest)
        old = flow.read(opened.account)
        self.assertEqual(totals(old), (2, 18))
        version = first.version
        self.assertEqual(visible(old), (
            ('A', version, 7, '合格', version, 'meter-A-open', version, 'quality-A-open'),
            ('B', version, 11, '合格', version, 'meter-B-open', version, 'quality-B-open'),
        ))
        with self.assertRaises(LookupError):
            flow.read(latest.account + 1)

    def test_reopen_materials_without_source(self):
        flow = self.flow
        first, opened = flow.open_day()
        amended, packets = flow.corrections()
        receipt_id = flow.receive(packets['meter'])
        flow.disconnect_source()
        flow.reopen_warehouse()
        self.assertFalse(flow.source_path.exists())
        reconstructed = {material.receipt_id: material.envelope for material in
                         list_day_materials(flow.warehouse, flow.site, flow.day)}
        self.assertEqual(reconstructed[receipt_id], decode_envelope(packets['meter']))
        middle = flow.publish()
        self.assertEqual(totals(middle.view), (2, 24))
        self.assertEqual([(row.row, row.version) for row in middle.view.rows],
                         [('A', first.version), ('B', amended.version)])
        self.assertEqual(middle.view.rows[1].quality_origin, 'quality-B-open')
        late_receipt = flow.receive(packets['quality'])
        complete = flow.publish()
        self.assertEqual(totals(complete.view), (2, 30))
        self.assertEqual(complete.view.rows[0].quality_origin, 'quality-A-amend')
        flow.reopen_warehouse()
        self.assertEqual(flow.receive(packets['meter']), receipt_id)
        self.assertEqual(flow.receive(packets['quality']), late_receipt)
        self.assertEqual(flow.read(opened.account), opened)
        self.assertEqual(flow.read(middle.view.account), middle.view)
        self.assertEqual(flow.read(), complete.view)
