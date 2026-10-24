"""Rebuild Kalshi order books from ``book_l2`` rows.

    from kalshieye import replay_book
    for ts, ticker, bids, asks in replay_book(df, ticker="KXBTC15M-26OCT121545-45"):
        ...  # bids/asks: [(price, size), ...] best first, YES terms

Kalshi books are bids only. A NO bid at p is a YES ask at 1 - p, so asks here
are derived from the NO side.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator


class Book:
    """One ticker's book. ``apply`` takes book_l2 rows in file order."""

    def __init__(self) -> None:
        self.yes: dict[float, float] = {}
        self.no: dict[float, float] = {}
        self._snap_ts: Any = None

    def apply(self, row: dict[str, Any]) -> None:
        side, price, size = row.get("side"), row.get("price"), row.get("size")
        if row.get("is_snapshot"):
            if row.get("ts_recv") != self._snap_ts:
                self.yes.clear()
                self.no.clear()
                self._snap_ts = row.get("ts_recv")
            if side is None or price is None:
                return
            book = self.yes if side == "yes" else self.no
            if size and size > 0:
                book[float(price)] = float(size)
            else:
                book.pop(float(price), None)
            return
        self._snap_ts = None
        if side is None or price is None or size is None:
            return
        book = self.yes if side == "yes" else self.no
        p = float(price)
        # Sizes are fractional; rounding keeps float residue from leaving a phantom level.
        left = round(book.get(p, 0.0) + float(size), 6)
        if left > 0:
            book[p] = left
        else:
            book.pop(p, None)

    def bids(self, levels: int = 10) -> list[tuple[float, float]]:
        return sorted(self.yes.items(), reverse=True)[:levels]

    def asks(self, levels: int = 10) -> list[tuple[float, float]]:
        return [(round(1.0 - p, 6), s) for p, s in sorted(self.no.items(), reverse=True)[:levels]]

    def best(self) -> tuple[float | None, float | None]:
        b = max(self.yes) if self.yes else None
        a = round(1.0 - max(self.no), 6) if self.no else None
        return b, a


def _rows(data: Any) -> Iterable[dict[str, Any]]:
    if hasattr(data, "to_pylist"):
        return data.to_pylist()
    if hasattr(data, "to_dicts"):
        return data.to_dicts()
    if hasattr(data, "to_dict") and hasattr(data, "columns"):
        return data.to_dict("records")
    return data


def replay_book(
    data: Any, *, ticker: str | None = None, levels: int = 10
) -> Iterator[tuple[Any, str, list[tuple[float, float]], list[tuple[float, float]]]]:
    """Yield ``(ts_recv, ticker, bids, asks)`` after each book message.

    ``data`` is a pandas or polars DataFrame, a pyarrow Table, or an iterable of
    dicts with the book_l2 columns. Rows of one message share ts_recv; the state
    is yielded once the message is fully applied.
    """
    books: dict[str, Book] = {}
    pending: tuple[Any, str] | None = None
    want = ticker.upper() if ticker else None
    for row in _rows(data):
        t = str(row.get("ticker") or "")
        if want and t.upper() != want:
            continue
        key = (row.get("ts_recv"), t)
        if pending is not None and key != pending:
            b = books[pending[1]]
            yield pending[0], pending[1], b.bids(levels), b.asks(levels)
        books.setdefault(t, Book()).apply(row)
        pending = key
    if pending is not None:
        b = books[pending[1]]
        yield pending[0], pending[1], b.bids(levels), b.asks(levels)
