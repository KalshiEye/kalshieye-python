import pytest

from kalshieye import Book, book, replay_book


def _row(ts, side, price, size, snapshot=False, ticker="T"):
    return {"ts_recv": ts, "ticker": ticker, "is_snapshot": snapshot, "side": side, "price": price, "size": size}


def test_snapshot_then_deltas():
    rows = [
        _row(0, "yes", 0.40, 100.0, snapshot=True),
        _row(0, "no", 0.55, 50.0, snapshot=True),
        _row(1, "yes", 0.41, 20.0),
        _row(2, "yes", 0.40, -100.0),
        _row(3, "no", 0.55, -10.5),
    ]
    states = list(replay_book(rows))
    assert len(states) == 4  # one per message
    _, ticker, bids, asks = states[-1]
    assert ticker == "T"
    assert bids == [(0.41, 20.0)]
    assert asks == [(0.45, 39.5)]  # a NO bid at 0.55 is a YES ask at 0.45


def test_snapshot_replaces_the_book():
    book = Book()
    book.apply(_row(0, "yes", 0.30, 5.0, snapshot=True))
    book.apply(_row(1, "yes", 0.31, 5.0))
    book.apply(_row(2, "yes", 0.20, 7.0, snapshot=True))
    assert book.bids() == [(0.20, 7.0)]


def test_empty_snapshot_clears_the_book():
    book = Book()
    book.apply(_row(0, "yes", 0.30, 5.0, snapshot=True))
    book.apply(_row(1, None, None, None, snapshot=True))
    assert book.best() == (None, None)


def test_float_residue_does_not_leave_a_level():
    # These sizes sum to exactly zero, but adding them as floats leaves about 1.4e-9.
    sizes = [1121596.98, 1628450.55, 1595362.86, 1258899.31, -1569585.22, -4034724.48]
    rows = [_row(0, "yes", 0.52, 10.0, snapshot=True), _row(0, "yes", 0.53, sizes[0], snapshot=True)]
    rows += [_row(i, "yes", 0.53, size) for i, size in enumerate(sizes[1:], start=1)]
    *_, (ts, ticker, bids, asks) = replay_book(rows)
    assert bids == [(0.52, 10.0)]


def test_filters_one_ticker():
    rows = [_row(0, "yes", 0.30, 5.0, snapshot=True, ticker="A"), _row(0, "yes", 0.60, 9.0, snapshot=True, ticker="B")]
    assert [s[1] for s in replay_book(rows, ticker="b")] == ["B"]


def test_tables_and_frames_replay_like_rows(monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pd = pytest.importorskip("pandas")
    monkeypatch.setattr(book, "_BATCH", 64)  # many batches, so rows cross batch boundaries
    rows = [_row(0, "yes", 0.40, 100.0, snapshot=True), _row(0, "no", 0.55, 50.0, snapshot=True)]
    rows += [_row(i, "yes", 0.40 + (i % 3) / 100, float(i % 7) - 3.0) for i in range(1, 2000)]
    want = list(replay_book(rows))
    assert list(replay_book(pa.Table.from_pylist(rows))) == want
    assert list(replay_book(pd.DataFrame(rows))) == want
