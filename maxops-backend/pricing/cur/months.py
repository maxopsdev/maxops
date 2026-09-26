"""Month helpers for CUR cache refresh planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterator


@dataclass(frozen=True, order=True)
class BillingMonth:
    year: int
    month: int

    @classmethod
    def parse(cls, value: str) -> "BillingMonth":
        year, month = value.split("-", 1)
        parsed = cls(int(year), int(month))
        if parsed.month < 1 or parsed.month > 12:
            raise ValueError(f"Invalid billing month: {value}")
        return parsed

    @classmethod
    def current(cls, now: datetime | None = None) -> "BillingMonth":
        now = now or datetime.now(timezone.utc)
        return cls(now.year, now.month)

    def add(self, months: int) -> "BillingMonth":
        zero_based = self.year * 12 + (self.month - 1) + months
        return BillingMonth(zero_based // 12, zero_based % 12 + 1)

    def start_date(self) -> date:
        return date(self.year, self.month, 1)

    def end_date(self) -> date:
        return self.add(1).start_date()

    def previous(self) -> "BillingMonth":
        return self.add(-1)

    def is_previous_month_grace(self, now: datetime, grace_days: int) -> bool:
        if grace_days <= 0 or self != BillingMonth.current(now).previous():
            return False
        return now.day <= grace_days

    def path_parts(self) -> tuple[str, str]:
        return f"year={self.year:04d}", f"month={self.month:02d}"

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def month_range(start: BillingMonth, end: BillingMonth) -> Iterator[BillingMonth]:
    current = start
    while current <= end:
        yield current
        current = current.add(1)
