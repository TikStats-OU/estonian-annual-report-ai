"""In-memory ledger with validation at the write boundary."""

from collections.abc import Iterable

from .models import Account, JournalEntry


class Ledger:
    """A small ledger that accepts only balanced entries for known accounts."""

    def __init__(self, accounts: Iterable[Account]) -> None:
        self._accounts: dict[str, Account] = {}
        for account in accounts:
            if account.code in self._accounts:
                raise ValueError(f"Duplicate account code: {account.code}.")
            self._accounts[account.code] = account
        self._entries: list[JournalEntry] = []
        self._entry_ids: set[str] = set()

    @property
    def accounts(self) -> tuple[Account, ...]:
        return tuple(self._accounts.values())

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def get_account(self, code: str) -> Account:
        try:
            return self._accounts[code]
        except KeyError as exc:
            raise ValueError(f"Unknown account code: {code}.") from exc

    def add_entry(self, entry: JournalEntry) -> None:
        if entry.entry_id in self._entry_ids:
            raise ValueError(f"Duplicate journal entry ID: {entry.entry_id}.")
        unknown_codes = sorted(
            {line.account_code for line in entry.lines if line.account_code not in self._accounts}
        )
        if unknown_codes:
            raise ValueError(f"Unknown account code(s): {', '.join(unknown_codes)}.")
        self._entries.append(entry)
        self._entry_ids.add(entry.entry_id)
