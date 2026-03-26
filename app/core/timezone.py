from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat_datetime(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
