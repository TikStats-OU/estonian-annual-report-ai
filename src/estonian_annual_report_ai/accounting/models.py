"""Small immutable accounting value objects."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from .money import to_minor_units


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

    @property
    def normal_side(self) -> str:
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return "debit"
        return "credit"


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    type: AccountType

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("Account code cannot be empty.")
        if not self.name.strip():
            raise ValueError("Account name cannot be empty.")
        if not isinstance(self.type, AccountType):
            raise TypeError("Account type must be an AccountType.")


@dataclass(frozen=True, slots=True)
class JournalLine:
    account_code: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not self.account_code.strip():
            raise ValueError("Journal line account code cannot be empty.")
        for field_name, value in (("debit", self.debit), ("credit", self.credit)):
            if not isinstance(value, Decimal):
                raise TypeError(f"{field_name} must be a Decimal.")
            if not value.is_finite():
                raise ValueError(f"{field_name} must be finite.")
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative.")
            to_minor_units(value)
        if (self.debit == 0) == (self.credit == 0):
            raise ValueError("A journal line must have a positive debit or credit, but not both.")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    entry_id: str
    entry_date: date
    description: str
    lines: tuple[JournalLine, ...]
    source_reference: str | None = None
    is_closing_entry: bool = False

    def __post_init__(self) -> None:
        if not self.entry_id.strip():
            raise ValueError("Journal entry ID cannot be empty.")
        if not isinstance(self.entry_date, date):
            raise TypeError("Journal entry date must be a date.")
        if not self.description.strip():
            raise ValueError("Journal entry description cannot be empty.")
        lines = tuple(self.lines)
        if len(lines) < 2:
            raise ValueError("A journal entry must contain at least two lines.")
        if any(not isinstance(line, JournalLine) for line in lines):
            raise TypeError("Journal entry lines must be JournalLine instances.")
        debits = sum(to_minor_units(line.debit) for line in lines)
        credits = sum(to_minor_units(line.credit) for line in lines)
        if debits != credits:
            raise ValueError(
                f"Journal entry is not balanced: debits={debits} cents, credits={credits} cents."
            )
        object.__setattr__(self, "lines", lines)
