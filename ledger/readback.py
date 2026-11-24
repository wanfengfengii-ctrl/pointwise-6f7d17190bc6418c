from ledger.settlement import export_settlement
from ledger.snapshots import LedgerSnapshot, load_current, load_snapshot
from source.origins import require_number, validate_station_day


LedgerView = LedgerSnapshot


def read(db, *, site=None, day=None, account=None):
    if account is not None:
        if site is not None or day is not None:
            raise ValueError('read accepts a station day or an account')
        require_number(account, 'ledger account')
        return load_snapshot(db, account)
    validate_station_day(site, day)
    return load_current(db, site, day)
