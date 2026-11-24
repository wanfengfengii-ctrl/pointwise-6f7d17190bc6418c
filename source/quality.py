from enum import StrEnum


class QualityVerdict(StrEnum):
    ACCEPTED = '合格'
    REVIEW = '待复核'


def validate_verdict(value):
    if not isinstance(value, str):
        raise ValueError('quality must be 合格 or 待复核')
    return QualityVerdict(value).value
