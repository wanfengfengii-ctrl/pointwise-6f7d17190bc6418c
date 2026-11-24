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


_SAMPLE_DIR = Path(__file__).resolve().parent / 'samples'


def read_sample(name):
    if name not in ('initial', 'corrections', 'handover'):
        raise ValueError('unknown sample')
    return json.loads((_SAMPLE_DIR / f'{name}.json').read_text(encoding='utf-8'))


def _book(db, view):
    restored = read(db, account=view.account)
    return {
        'account': restored.account,
        'measured_count': restored.totals.measured_count,
        'total_liters': restored.totals.total_liters,
        'rows': [asdict(row) for row in restored.rows],
    }


def _round(db, issuers, site, day, data, item_numbers):
    entries = tuple(record(db, site, day, **entry) for entry in data)
    frozen = freeze_day(db, site, day, [entry.entry_id for entry in entries])
    return {
        column: issue_column(issuers[column], frozen.version, column, item_numbers[column],
                             projection=project_frozen_column(db, frozen.version, column))
        for column in issuers
    }


def demo(directory):
    initial = read_sample('initial')
    amendment = read_sample('corrections')
    numbers = read_sample('handover')['issues']
    site, day = initial['site'], initial['day']
    warehouse_path = directory / 'warehouse.sqlite'
    with ExitStack() as stack:
        source = stack.enter_context(closing(open_source(directory / 'source.sqlite')))
        issuers = {
            column: stack.enter_context(closing(open_issuer(directory / f'{column}.sqlite', column)))
            for column in ('meter', 'quality')
        }
        warehouse = stack.enter_context(closing(open_warehouse(warehouse_path)))
        first_items = _round(source, issuers, site, day, initial['records'], numbers['initial'])
        for item in first_items.values():
            admit(warehouse, item)
        first = publish(warehouse, site, day).view
        amended_items = _round(source, issuers, site, day, amendment['records'], numbers['corrected'])
        receipt = admit(warehouse, amended_items['meter'])

    with closing(open_warehouse(warehouse_path)) as warehouse:
        middle = publish(warehouse, site, day)
        admit(warehouse, amended_items['quality'])
        final = publish(warehouse, site, day).view
        replay = admit(warehouse, amended_items['meter'])
        books = {
            'initial': _book(warehouse, first),
            'partial': _book(warehouse, middle.view),
            'current': _book(warehouse, final),
        }
    expected = {'initial_liters': 18, 'partial_liters': 24, 'current_liters': 30}
    actual = {f'{phase}_liters': book['total_liters'] for phase, book in books.items()}
    partial_rows = [[row.row, row.reading, row.verdict, row.quality_origin] for row in middle.view.rows]
    downstream_expected = {
        'partial_rows': [['A', 7, '合格', 'quality-A-open'], ['B', 17, '合格', 'quality-B-open']],
        'same_receipt': True,
    }
    downstream_actual = {'partial_rows': partial_rows, 'same_receipt': receipt == replay}
    if actual != expected or downstream_actual != downstream_expected:
        raise RuntimeError('sample ledger disagrees with its hand calculation')
    print(json.dumps(books, ensure_ascii=False, sort_keys=True))
    scenario = {
        'case_id': 'v7.reopen_materials',
        'input': {'initial': initial['records'], 'amendment': amendment['records'],
                  'delivery': ['meter', 'reopen warehouse', 'publish', 'quality', 'publish']},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['intake/materials.py', 'ledger/eligibility.py', 'ledger/publish.py',
                       'ledger/totals.py', 'ledger/readback.py'],
        'oracle': '逐行手算 7+11、7+17、13+17，并用样例的原质控出处核对中间账。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, sort_keys=True))
    # 勘误结清回执：两条出版履历的真实导出、送达与离线结清结果。
    from settlement_demo import run_settlement_demo
    settlement = run_settlement_demo(directory / 'settlement')
    print('SETTLEMENT ' + json.dumps(settlement, ensure_ascii=False, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description='青岔河分栏观测日仓')
    commands = parser.add_subparsers(dest='command', required=True)
    sample = commands.add_parser('demo', help='run the station-day handover sample')
    sample.add_argument('--directory', type=Path, help='empty directory for sample databases')
    options = parser.parse_args()
    directory = options.directory
    if directory is None:
        with TemporaryDirectory(prefix='qingcha-demo-') as temporary:
            demo(Path(temporary))
    else:
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            parser.error('the demo needs an empty directory')
        directory.mkdir(parents=True, exist_ok=True)
        demo(directory)


if __name__ == '__main__':
    main()
