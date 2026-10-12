"""Deterministic financial statements derived from a ledger."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ledger import Ledger
from .models import AccountType, JournalEntry
from .money import from_minor_units, subtract_money, sum_money, to_minor_units


@dataclass(frozen=True, slots=True)
class TrialBalanceLine:
    account_code: str
    account_name: str
    account_type: AccountType
    debit_balance: Decimal
    credit_balance: Decimal


@dataclass(frozen=True, slots=True)
class TrialBalance:
    through_date: date | None
    lines: tuple[TrialBalanceLine, ...]
    total_debits: Decimal
    total_credits: Decimal


@dataclass(frozen=True, slots=True)
class StatementLine:
    account_code: str
    name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    period_start: date
    period_end: date
    revenue: tuple[StatementLine, ...]
    expenses: tuple[StatementLine, ...]
    total_revenue: Decimal
    total_expenses: Decimal
    net_profit: Decimal


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    as_of: date
    period_start: date
    assets: tuple[StatementLine, ...]
    liabilities: tuple[StatementLine, ...]
    equity: tuple[StatementLine, ...]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal

    @property
    def total_liabilities_and_equity(self) -> Decimal:
        return sum_money((self.total_liabilities, self.total_equity))

    @property
    def is_balanced(self) -> bool:
        return to_minor_units(self.total_assets) == to_minor_units(self.total_liabilities_and_equity)


def _entries_through(ledger: Ledger, through_date: date | None) -> tuple[JournalEntry, ...]:
    if through_date is None:
        return ledger.entries
    return tuple(entry for entry in ledger.entries if entry.entry_date <= through_date)


def _balances(
    ledger: Ledger, entries: tuple[JournalEntry, ...]
) -> dict[str, tuple[Decimal, Decimal]]:
    totals: dict[str, list[int]] = defaultdict(
        lambda: [0, 0]
    )
    for entry in entries:
        for line in entry.lines:
            totals[line.account_code][0] += to_minor_units(line.debit)
            totals[line.account_code][1] += to_minor_units(line.credit)
    return {
        account.code: (from_minor_units(totals[account.code][0]), from_minor_units(totals[account.code][1]))
        for account in ledger.accounts
    }


def _normal_balance(account_type: AccountType, debits: Decimal, credits: Decimal) -> Decimal:
    if account_type.normal_side == "debit":
        return subtract_money(debits, credits)
    return subtract_money(credits, debits)


def trial_balance(ledger: Ledger, through_date: date | None = None) -> TrialBalance:
    entries = _entries_through(ledger, through_date)
    balances = _balances(ledger, entries)
    lines: list[TrialBalanceLine] = []
    for account in ledger.accounts:
        debits, credits = balances[account.code]
        debit_balance = max(debits - credits, Decimal(0))
        credit_balance = max(credits - debits, Decimal(0))
        lines.append(
            TrialBalanceLine(
                account_code=account.code,
                account_name=account.name,
                account_type=account.type,
                debit_balance=debit_balance,
                credit_balance=credit_balance,
            )
        )
    return TrialBalance(
        through_date=through_date,
        lines=tuple(lines),
        total_debits=sum_money(line.debit_balance for line in lines),
        total_credits=sum_money(line.credit_balance for line in lines),
    )


def income_statement(
    ledger: Ledger, period_start: date, period_end: date
) -> IncomeStatement:
    if period_end < period_start:
        raise ValueError("Period end cannot be earlier than period start.")
    entries = tuple(
        entry
        for entry in ledger.entries
        if period_start <= entry.entry_date <= period_end and not entry.is_closing_entry
    )
    balances = _balances(ledger, entries)

    revenue: list[StatementLine] = []
    expenses: list[StatementLine] = []
    for account in ledger.accounts:
        if account.type not in (AccountType.REVENUE, AccountType.EXPENSE):
            continue
        amount = _normal_balance(account.type, *balances[account.code])
        if amount == 0:
            continue
        line = StatementLine(account.code, account.name, amount)
        if account.type == AccountType.REVENUE:
            revenue.append(line)
        else:
            expenses.append(line)

    total_revenue = sum_money(line.amount for line in revenue)
    total_expenses = sum_money(line.amount for line in expenses)
    return IncomeStatement(
        period_start=period_start,
        period_end=period_end,
        revenue=tuple(revenue),
        expenses=tuple(expenses),
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        net_profit=subtract_money(total_revenue, total_expenses),
    )


def balance_sheet(
    ledger: Ledger, as_of: date, period_start: date
) -> BalanceSheet:
    if as_of < period_start:
        raise ValueError("Balance Sheet date cannot be earlier than the period start.")
    balance_entries = tuple(
        entry for entry in _entries_through(ledger, as_of)
        if not (period_start <= entry.entry_date <= as_of and entry.is_closing_entry)
    )
    balances = _balances(ledger, balance_entries)
    groups: dict[AccountType, list[StatementLine]] = {
        AccountType.ASSET: [],
        AccountType.LIABILITY: [],
        AccountType.EQUITY: [],
    }
    for account in ledger.accounts:
        if account.type not in groups:
            continue
        amount = _normal_balance(account.type, *balances[account.code])
        if amount != 0:
            groups[account.type].append(
                StatementLine(account.code, account.name, amount)
            )

    period_result = income_statement(ledger, period_start, as_of).net_profit
    groups[AccountType.EQUITY].append(
        StatementLine("CURRENT_PERIOD_RESULT", "Current period profit / (loss)", period_result)
    )

    assets = tuple(groups[AccountType.ASSET])
    liabilities = tuple(groups[AccountType.LIABILITY])
    equity = tuple(groups[AccountType.EQUITY])
    total_assets = sum_money(line.amount for line in assets)
    total_liabilities = sum_money(line.amount for line in liabilities)
    total_equity = sum_money(line.amount for line in equity)
    return BalanceSheet(
        as_of=as_of,
        period_start=period_start,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
    )
