import json

from ledger.snapshots import load_snapshot
from source.origins import require_number


# 回执 schema：
# {
#   "schema": 2,
#   "account": 固定账号,
#   "site": 站点,
#   "day": 站日,
#   "previous_account": 同一站日前缀账号（首发为 null）,
#   "rows": [
#     {
#       "row": 行键,
#       "version": 该账实际出版的行版,
#       "meter_version": 本行该账使用的读数栏正文版次,
#       "quality_version": 本行该账使用的质控栏正文版次,
#       "reading": 读数（整数或 null）,
#       "verdict": 质控判定,
#       "meter_origin": 读数栏正文出处,
#       "quality_origin": 质控栏正文出处
#     }
#   ]
# }
#
# 覆盖含义：previous_account 声明同一站日账链中的前一固定账号；rows 声明该账号
# 实际出版的全部行及其使用的两栏正文版次（版次即该栏正文的冻结日版，身份由
# 站日、行键、栏别和该栏正文最初的冻结版次确定）。源端据 previous_account 证明
# 本站日连续出版前缀，据 rows 与本地冻结沿用关系核对每条改栏的结局。
RECEIPT_SCHEMA = 2


def _row_payload(row):
    return {
        'row': row.row,
        'version': row.version,
        'meter_version': row.meter_version,
        'quality_version': row.quality_version,
        'reading': row.reading,
        'verdict': row.verdict,
        'meter_origin': row.meter_origin,
        'quality_origin': row.quality_origin,
    }


def _previous_account(db, site, day, account):
    record = db.execute('''
        SELECT MAX(account) AS previous FROM ledger_snapshots
        WHERE site = ? AND day = ? AND account < ?
    ''', (site, day, account)).fetchone()
    return record['previous']


def export_settlement(db, account):
    """按有效固定账号出具可重取的出版结清回执（JSON 文本）。

    仅已提交的固定账可导出；账号沿用地仓全局编号。重取内容逐字节一致，
    未知账号抛 LookupError。
    """
    require_number(account, 'ledger account')
    snapshot = load_snapshot(db, account)
    previous = _previous_account(db, snapshot.site, snapshot.day, account)
    payload = {
        'schema': RECEIPT_SCHEMA,
        'account': snapshot.account,
        'site': snapshot.site,
        'day': snapshot.day,
        'previous_account': previous,
        'rows': [_row_payload(row) for row in snapshot.rows],
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':'))
