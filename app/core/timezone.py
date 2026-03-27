from datetime import UTC, date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_business_date(now: datetime | None = None) -> date:
    current = now or utc_now()
    return current.astimezone(KST).date()


def isoformat_datetime(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["KST", "get_business_date", "isoformat_datetime", "utc_now"]
