from freeze.days import freeze_day
from freeze.history import load_frozen_day
from freeze.projection import project_frozen_column
from issue.envelope import decode_envelope
from source.observations import record
from source.pending import list_pending, load_entry
from tests.support import WorkflowTest, totals, visible


class InitialTests(WorkflowTest):
    def test_initial_origins_and_totals(self):
        flow = self.flow
        registrations = flow.register_initial()
        source_refs = {('A', 'meter'): 'meter-A-open', ('A', 'quality'): 'quality-A-open',
                       ('B', 'meter'): 'meter-B-open', ('B', 'quality'): 'quality-B-open'}
        self.assertEqual({(entry.row, entry.column): entry.origin.reference for entry in registrations}, source_refs)
        frozen = flow.freeze(registrations)
        for entry in registrations:
            archived = load_entry(flow.source, entry.entry_id)
            self.assertEqual((archived.origin, archived.archived_version), (entry.origin, frozen.version))
        for column in ('meter', 'quality'):
            projected = project_frozen_column(flow.source, frozen.version, column)
            self.assertEqual({(body.row, body.column): body.origin for body in projected.bodies},
                             {key: name for key, name in source_refs.items() if key[1] == column})
        packets = flow.issue(frozen, 'initial')
        flow.receive(packets['meter'])
        missing = flow.publish()
        self.assertIsNone(missing.view.account)
        self.assertEqual(totals(missing.view), (0, 0))
        self.assertEqual([(row.row, row.missing) for row in missing.pending],
                         [('A', ('quality',)), ('B', ('quality',))])
        flow.receive(packets['quality'])
        published = flow.publish()
        version = frozen.version
        self.assertEqual(published.pending, ())
        self.assertEqual(totals(published.view), (2, 18))
        self.assertEqual(visible(flow.read(published.view.account)), (
            ('A', version, 7, '合格', version, 'meter-A-open', version, 'quality-A-open'),
            ('B', version, 11, '合格', version, 'meter-B-open', version, 'quality-B-open'),
        ))

    def test_explicit_missing_is_published(self):
        frozen, view = self.flow.open_day({'A': None})
        self.assertEqual(totals(view), (1, 11))
        self.assertEqual([(row.row, row.reading) for row in view.rows], [('A', None), ('B', 11)])
        self.assertEqual(view.rows[0].meter_version, frozen.version)
        self.assertEqual(self.flow.read(view.account).rows[0].quality_origin, 'quality-A-open')

    def test_zero_is_measured(self):
        _, view = self.flow.open_day({'A': None, 'B': 0})
        self.assertEqual(totals(view), (1, 0))
        self.assertEqual([row.reading for row in self.flow.read().rows], [None, 0])

    def test_invalid_registration_and_freeze_scope(self):
        flow = self.flow
        for column, value in [('meter', True), ('meter', 3.5), ('meter', '7'), ('quality', '未知')]:
            with self.subTest(column=column, value=value), self.assertRaises(ValueError):
                flow.add('A', column, value, 'invalid-origin')
        with self.assertRaises(ValueError):
            flow.add('missing-row', 'meter', 7, 'origin', correction='invalid')
        self.assertEqual(list_pending(flow.source, flow.site, flow.day), ())
        entries = flow.register_initial()
        for ids in ([entries[0].entry_id], [entries[0].entry_id] * 2, [999], []):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                freeze_day(flow.source, flow.site, flow.day, ids)
        with self.assertRaises(LookupError):
            load_frozen_day(flow.source, 1)
        self.assertEqual(list_pending(flow.source, flow.site, flow.day), entries)
        frozen = flow.freeze(entries[:1] + entries[2:3])
        self.assertEqual([row.row for row in frozen.rows], ['A'])
        self.assertEqual(list_pending(flow.source, flow.site, flow.day), entries[1:2] + entries[3:4])
        remaining = flow.freeze()
        self.assertEqual([row.row for row in remaining.rows], ['B'])
        for raw in flow.issue(frozen, 'initial').values():
            flow.receive(raw)
        for raw in flow.issue(remaining, 'corrected').values():
            flow.receive(raw)
        self.assertEqual(totals(flow.publish().view), (2, 18))

    def test_reopen_source(self):
        flow = self.flow
        entries = flow.register_initial()
        flow.reopen_source()
        self.assertEqual(list_pending(flow.source, flow.site, flow.day), entries)
        frozen = flow.freeze(entries)
        flow.reopen_source()
        self.assertEqual(load_frozen_day(flow.source, frozen.version), frozen)
        self.assertEqual(list_pending(flow.source, flow.site, flow.day), ())
        for raw in flow.issue(frozen, 'initial').values():
            flow.receive(raw)
        self.assertEqual(totals(flow.publish().view), (2, 18))
        self.assertEqual(load_entry(flow.source, entries[0].entry_id).origin.reference, 'meter-A-open')

    def test_station_day_keys_do_not_cross(self):
        flow = self.flow
        other_day = '2026-11-19'
        first = record(flow.source, flow.site, other_day, 'A', 'meter', 99, 'another-day-meter')
        second = record(flow.source, flow.site, other_day, 'A', 'quality', '合格', 'another-day-quality')
        with self.assertRaises(ValueError):
            freeze_day(flow.source, flow.site, flow.day, [first.entry_id, second.entry_id])
        other = freeze_day(flow.source, flow.site, other_day)
        raw = flow.issue(other, 'late')
        self.assertEqual(decode_envelope(raw['meter']).day, other_day)
        for item in raw.values():
            flow.receive(item)
        self.assertEqual(totals(flow.publish().view), (0, 0))
        _, opened = flow.open_day()
        self.assertEqual(totals(opened), (2, 18))
