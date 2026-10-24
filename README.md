# kalshieye

Python client for Kalshi Eye flat files: daily Parquet for the order book (`book_l2`), 1-second top-10 book snapshots (`book_snap_1s`), top of book (`bbo`), one-minute bars (`bbo_1m`), trades, crypto spot (`spot`), and the reference tables (`markets`, `settlements`, `series`, `incidents`).

```bash
pip install "kalshieye[pandas]"      # or kalshieye[polars], or plain kalshieye
export KALSHIEYE_API_KEY=clab_...     # create one under API keys
```

```python
from kalshieye import Client, replay_book

kx = Client()
kx.datasets()                                             # schemas, coverage, required plan
kx.estimate(["bbo_1m", "trades"], "2026-10-12", "2026-10-18", series=["KXBTC*"])

kx.download(["bbo_1m", "trades"], "2026-10-12", "2026-10-18",
            series=["KXBTC*"], dest="data", progress=True)

df = kx.load("bbo_1m", "2026-10-12", "2026-10-18", series="KXBTC15M")
fills = kx.load("trades", "2026-10-12", "2026-10-12", tickers=["KXBTC15M-26OCT121545-45"])

# Rebuild the book from book_l2 rows
book = kx.load("book_l2", "2026-10-12", "2026-10-12", tickers=["KXBTC15M-26OCT121545-45"])
for ts, ticker, bids, asks in replay_book(book):
    ...

# One event in one zip: book, snapshots, trades, spot, metadata.json, settlement.json
kx.bundle("KXBTC15M-26OCT121545")

# CSV.gz copy of a single file
f = next(kx.files("trades", "2026-10-12", "2026-10-12", series="KXBTC15M"))
kx.csv(f["key"])

# S3 access (paid plans): a read-only key for aws s3 sync, rclone, DuckDB, boto3
key = kx.create_s3_key("laptop")
print(key["snippets"]["aws_cli"])
```

- Files land under `dest` in the lake layout (`bbo_1m/date=2026-10-12/series=KXBTC15M/...parquet`), so DuckDB, Polars, and Spark can read the folder as a hive-partitioned dataset. `spot` is partitioned by `asset=` instead of `series=`.
- Every file is checked against its sha256. Files already on disk are skipped and do not count toward your monthly download allowance.
- `series` accepts wildcards (`KXBTC*`). `categories` filters by archive category (`crypto`, `politics`, ...). `tickers` picks the series files holding those markets, and `load` keeps only their rows.
- `bundle`, `csv`, and `replay` are built on the server the first time; the client waits for them.
- Errors raise `kalshieye.ApiError`. Use `err.code` to tell plan limits (`plan_required`, `history_limited`, `download_quota`) apart.

## Without an account

`replay_book` works on any `book_l2` rows, including the free one-day sample on Hugging Face ([KalshiEye/kalshi-order-book-sample](https://huggingface.co/datasets/KalshiEye/kalshi-order-book-sample)), which DuckDB reads in place:

```python
# pip install "kalshieye[pandas]" duckdb
import duckdb
from kalshieye import replay_book

# Bills vs Rams, YES if Buffalo wins: every book change up to 11 PM Eastern
book = duckdb.sql("""
  SELECT * FROM 'hf://datasets/KalshiEye/kalshi-order-book-sample/kalshieye-data/book_l2/*/series=KXNFLGAME/*.parquet'
  WHERE ticker = 'KXNFLGAME-26OCT12BUFLAR-BUF'
    AND ts_recv::TIMESTAMPTZ <= '2026-10-13 03:00:00+00'::TIMESTAMPTZ
  ORDER BY ts_recv, seq
""").df()

*_, (ts, ticker, bids, asks) = replay_book(book, levels=3)
print(bids)  # [(0.51, 155253.51), (0.5, 405328.0), (0.49, 15126.0)]
print(asks)  # [(0.52, 960851.07), (0.53, 862817.41), (0.54, 113351.03)]
```

More runnable examples (a backtest, spreads and depth, calibration) are in [KalshiEye/kalshieye-examples](https://github.com/KalshiEye/kalshieye-examples). Every column is described in the [data dictionary](https://kalshieye.com/docs/datasets/).
