# 勘误结清回执演示：两条出版履历经真实导出、送达与离线查询得到不同结清结果。
from contextlib import ExitStack, closing
from dataclasses import dataclass
import json
from pathlib import Path

from intake.store import open_warehouse
from issue.register import open_issuer
from ledger.readback import export_settlement
from settlement_fixture import SITE, DAY, build_history
from source.pending import admit_settlement, open_source, read_settlement


@dataclass(frozen=True)
class HistoryCase:
    key: str
    publish_c2: bool
    upstream_accounts: tuple[int, ...]
    delivery_order: tuple[int, ...]


CASES = (
    HistoryCase(
        key='published_c2',
        publish_c2=True,
        upstream_accounts=(1, 2, 3),
        delivery_order=(3, 1, 2),
    ),
    HistoryCase(
        key='skipped_c2',
        publish_c2=False,
        # 2 号固定账属于下游站，证明上游站前缀只需覆盖 1 号与 3 号。
        upstream_accounts=(1, 3),
        delivery_order=(3, 1),
    ),
)


def _settlement_view(result):
    return {
        'account': result['account'],
        'entries': [
            {
                'correction': entry['correction'],
                'row': entry['row'],
                'column': entry['column'],
                'version': entry['version'],
                'state': entry['state'],
                'decided_at': entry['decided_at'],
            }
            for entry in result['entries']
        ],
    }


def _run_case(base: Path, case: HistoryCase):
    directory = base / case.key
    directory.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        source = stack.enter_context(closing(open_source(directory / 'source.sqlite')))
        warehouse = stack.enter_context(closing(open_warehouse(directory / 'warehouse.sqlite')))
        issuers = {
            column: stack.enter_context(
                closing(open_issuer(directory / f'{column}.sqlite', column)))
            for column in ('meter', 'quality')
        }
        build_history(source, warehouse, issuers, publish_c2=case.publish_c2)
        receipts = {
            account: export_settlement(warehouse, account)
            for account in case.upstream_accounts
        }
        # 固定账号重取内容一致。
        for account, raw in receipts.items():
            if export_settlement(warehouse, account) != raw:
                raise AssertionError(f'account {account} receipt changed on re-fetch')

        checkpoints = []
        # 乱序送达：末账先到，前件未齐时保留空截点。
        for account in case.delivery_order:
            raw = receipts[account]
            admitted = admit_settlement(source, raw)
            if admitted != account:
                raise AssertionError((admitted, account))
            # 同账号同内容重投返回原受理结果（含空白变体）。
            if admit_settlement(source, ' ' + raw) != account:
                raise AssertionError(f'account {account} redelivery was not idempotent')
            checkpoint = read_settlement(source, site=SITE, day=DAY)
            checkpoints.append({'delivered': account, 'prefix_account': checkpoint['account']})

    # 关闭异地仓与出件库后，重开源库离线复查。
    for column in ('meter', 'quality'):
        (directory / f'{column}.sqlite').unlink()
    (directory / 'warehouse.sqlite').unlink()
    with closing(open_source(directory / 'source.sqlite')) as reopened:
        settled = read_settlement(reopened, site=SITE, day=DAY)
        # 重开源库后重投仍被受理且结果不变。
        if admit_settlement(reopened, receipts[case.delivery_order[0]]) != case.delivery_order[0]:
            raise AssertionError('redelivery after reopen changed the admission result')
        rechecked = read_settlement(reopened, site=SITE, day=DAY)
        if rechecked != settled:
            raise AssertionError('reopen changed the offline settlement result')
    return {
        'delivery_order': list(case.delivery_order),
        'prefix_checkpoints': checkpoints,
        'settlement': _settlement_view(settled),
    }


def _expected_states():
    return {
        'published_c2': {
            'C2:meter': ('used', 2),
            'C2:quality': ('used', 2),
            'C3:meter': ('used', 3),
            'C4:meter': ('pending', None),
        },
        'skipped_c2': {
            'C2:meter': ('skipped', 3),
            'C2:quality': ('used', 3),
            'C3:meter': ('used', 3),
            'C4:meter': ('pending', None),
        },
    }


def run_settlement_demo(base: Path):
    results = {case.key: _run_case(base, case) for case in CASES}
    expected = _expected_states()
    for case in CASES:
        settlement = results[case.key]['settlement']
        if settlement['account'] != 3:
            raise AssertionError((case.key, settlement['account']))
        actual = {
            f'{entry["correction"]}:{entry["column"]}': (entry['state'], entry['decided_at'])
            for entry in settlement['entries']
        }
        for key, want in expected[case.key].items():
            if actual.get(key) != want:
                raise AssertionError((case.key, key, actual.get(key), want))
    return results


def main():
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix='qingcha-settlement-demo-') as temporary:
        results = run_settlement_demo(Path(temporary))
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2))
    summary = {
        'case_id': 'settlement_receipts',
        'input': {
            'histories': [case.key for case in CASES],
            'delivery': {case.key: list(case.delivery_order) for case in CASES},
        },
        'expected': _expected_states(),
        'actual': {
            case.key: {
                'prefix_account': results[case.key]['settlement']['account'],
                'states': {
                    f'{entry["correction"]}:{entry["column"]}':
                        [entry['state'], entry['decided_at']]
                    for entry in results[case.key]['settlement']['entries']
                    if entry['correction'] in ('C2', 'C3', 'C4')
                },
            }
            for case in CASES
        },
        'code_paths': [
            'ledger/settlement.py', 'ledger/readback.py',
            'source/settlement.py', 'source/pending.py',
            'settlement_fixture.py', 'settlement_demo.py',
        ],
        'oracle': (
            '首版 7 L 合格出 1 号账；C2 改 13 L 待复核，C3 改 19 L。'
            '履历一 C2 两栏在 2 号账已用；履历二 C2 读数被 19 L 跨过、质控在 3 号账已用；'
            'C4 未送仓，均为未结清。'
        ),
    }
    print('POINTWISE_SCENARIO ' + json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
