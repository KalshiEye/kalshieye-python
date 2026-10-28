"""Small, dependency-free client for the Kalshi Eye data API.

    from kalshieye import Client

    kx = Client()                     # reads KALSHIEYE_API_KEY
    kx.estimate(["bbo_1m"], "2026-10-13", "2026-10-13", series=["KXBTC*"])
    paths = kx.download(["bbo_1m"], "2026-10-13", "2026-10-13", series=["KXBTC*"])   # ./kalshieye-data
    df = kx.load("bbo_1m", "2026-10-13", "2026-10-13", series=["KXBTC15M"])   # pandas
    df = kx.load("trades", "2026-10-13", "2026-10-13", tickers=["KXBTC15M-26OCT131200-00"])
    zip_path = kx.bundle("KXBTC15M-26OCT131200")      # one event: the datasets on your plan
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

DEFAULT_BASE_URL = "https://kalshieye.com"
_LINK_BATCH = 200


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: Any) -> None:
        self.status = status
        self.detail = detail
        if isinstance(detail, dict):
            message = detail.get("message") or json.dumps(detail)
        else:
            message = str(detail)
        super().__init__(f"{status}: {message}")

    @property
    def code(self) -> str | None:
        return self.detail.get("code") if isinstance(self.detail, dict) else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _as_list(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


class Client:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 2.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("KALSHIEYE_API_KEY")
        self.base_url = (base_url or os.environ.get("KALSHIEYE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        url = self.base_url + path
        if params:
            pairs: list[tuple[str, str]] = []
            for k, v in params.items():
                if v is None:
                    continue
                for item in v if isinstance(v, (list, tuple)) else [v]:
                    pairs.append((k, str(item).lower() if isinstance(item, bool) else str(item)))
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        headers = {"Accept": "application/json", "User-Agent": "kalshieye-python/0.2"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read()
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(text).get("detail", text)
            except (json.JSONDecodeError, AttributeError):
                detail = text
            raise ApiError(exc.code, detail) from None
        return json.loads(raw) if raw else None

    def _fetch(self, url: str, dest: Path) -> None:
        with urllib.request.urlopen(url, timeout=self.timeout) as res, dest.open("wb") as fh:
            shutil.copyfileobj(res, fh, length=1 << 20)

    def _need_key(self) -> None:
        if not self.api_key:
            raise ApiError(401, "Set KALSHIEYE_API_KEY or pass api_key= (create a key at https://kalshieye.com/app/api/).")

    def _poll(self, call: Callable[[], dict[str, Any]], *, wait: float) -> dict[str, Any]:
        """Repeat ``call`` while the server reports ``building``."""
        deadline = time.monotonic() + wait
        while True:
            out = call()
            status = out.get("status")
            if status == "error":
                raise ApiError(500, out.get("error") or "build failed")
            if status != "building" or time.monotonic() >= deadline:
                return out
            time.sleep(self.poll_interval)

    def _save(self, f: dict[str, Any], dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        self._fetch(f["url"], tmp)
        tmp.replace(dest)
        return dest

    # ------------------------------------------------------------------ catalog

    def datasets(self) -> list[dict[str, Any]]:
        """Every dataset with columns, coverage, required plan, and sample links."""
        return self._request("GET", "/api/v1/datasets")["datasets"]

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/datasets/{dataset_id}")

    def series(self, dataset_id: str, q: str | None = None, *, limit: int = 500) -> list[dict[str, Any]]:
        """Per-series coverage (days, rows, bytes, first/last date) for a dataset."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._request(
                "GET",
                f"/api/v1/datasets/{dataset_id}/series",
                params={"q": q, "offset": offset, "limit": min(limit, 500)},
            )
            out.extend(page["series"])
            offset += len(page["series"])
            if not page["series"] or offset >= page["total"]:
                return out

    def plan(self) -> dict[str, Any]:
        self._need_key()
        return self._request("GET", "/api/v1/plan")

    # ------------------------------------------------------------------ selection

    @staticmethod
    def _selection(
        datasets: str | Sequence[str],
        start_date: str,
        end_date: str,
        series: str | Iterable[str] | None,
        categories: str | Iterable[str] | None,
        tickers: str | Iterable[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "datasets": _as_list(datasets),
            "start_date": str(start_date),
            "end_date": str(end_date),
            "series": _as_list(series),
            "categories": _as_list(categories),
            "tickers": [t.upper() for t in _as_list(tickers)],
        }

    def estimate(
        self,
        datasets: str | Sequence[str],
        start_date: str,
        end_date: str,
        *,
        series: str | Iterable[str] | None = None,
        categories: str | Iterable[str] | None = None,
        tickers: str | Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Files, rows, and bytes for a selection, plus what your plan and allowance cover."""
        return self._request(
            "POST",
            "/api/v1/requests/estimate",
            body=self._selection(datasets, start_date, end_date, series, categories, tickers),
        )

    def files(
        self,
        datasets: str | Sequence[str],
        start_date: str,
        end_date: str,
        *,
        series: str | Iterable[str] | None = None,
        categories: str | Iterable[str] | None = None,
        tickers: str | Iterable[str] | None = None,
        page_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Yield file entries (dataset, date, series, key, path, rows, bytes, sha256). No links, no quota.

        ``tickers`` selects the series files that hold those markets; the files
        themselves hold the whole series.
        """
        self._need_key()
        sel = self._selection(datasets, start_date, end_date, series, categories, tickers)
        cursor: str | None = None
        while True:
            page = self._request(
                "GET",
                "/api/v1/files",
                params={
                    "dataset": sel["datasets"],
                    "start_date": sel["start_date"],
                    "end_date": sel["end_date"],
                    "series": sel["series"],
                    "category": sel["categories"],
                    "ticker": sel["tickers"],
                    "cursor": cursor,
                    "limit": page_size,
                },
            )
            yield from page["files"]
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def links(self, keys: Sequence[str]) -> list[dict[str, Any]]:
        """Signed links (valid one hour) for file keys. New files count toward your monthly GB."""
        self._need_key()
        out: list[dict[str, Any]] = []
        for i in range(0, len(keys), _LINK_BATCH):
            out.extend(self._request("POST", "/api/v1/files/links", body={"keys": list(keys[i : i + _LINK_BATCH])})["files"])
        return out

    def download(
        self,
        datasets: str | Sequence[str],
        start_date: str,
        end_date: str,
        *,
        series: str | Iterable[str] | None = None,
        categories: str | Iterable[str] | None = None,
        tickers: str | Iterable[str] | None = None,
        dest: str | Path = "kalshieye-data",
        progress: bool = False,
    ) -> list[Path]:
        """Download a selection into ``dest`` (lake layout), verify sha256, skip files already there."""
        root = Path(dest)
        entries = list(
            self.files(datasets, start_date, end_date, series=series, categories=categories, tickers=tickers)
        )
        paths: list[Path] = []
        need: list[dict[str, Any]] = []
        for f in entries:
            p = root / f["path"]
            paths.append(p)
            if not (p.is_file() and (not f.get("sha256") or _sha256(p) == f["sha256"])):
                need.append(f)
        for f in self.links([f["key"] for f in need]):
            p = root / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".part")
            self._fetch(f["url"], tmp)
            if f.get("sha256") and _sha256(tmp) != f["sha256"]:
                tmp.unlink()
                raise ApiError(0, f"checksum mismatch: {f['path']}")
            tmp.replace(p)
            if progress:
                print(f"{f['path']}  {f['bytes'] / 1e6:.1f} MB")
        return paths

    def load(
        self,
        dataset: str,
        start_date: str,
        end_date: str,
        *,
        series: str | Iterable[str] | None = None,
        categories: str | Iterable[str] | None = None,
        tickers: str | Iterable[str] | None = None,
        dest: str | Path = "kalshieye-data",
        engine: str = "pandas",
    ):
        """Download (if needed) and load one dataset: engine is "pandas", "polars", or "arrow".

        With ``tickers``, only those markets' rows are kept.
        """
        if engine not in ("pandas", "polars", "arrow"):
            raise ValueError("engine must be pandas, polars, or arrow")
        want = sorted({t.upper() for t in _as_list(tickers)})
        paths = self.download(
            dataset, start_date, end_date, series=series, categories=categories, tickers=want or None, dest=dest
        )
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        tables = []
        for p in paths:
            t = pq.read_table(p)
            if want and "ticker" in t.column_names:
                col = t.column("ticker")
                if pa.types.is_dictionary(col.type):
                    col = col.cast(pa.string())
                t = t.filter(pc.is_in(col, value_set=pa.array(want)))
            tables.append(t)
        table = pa.concat_tables(tables, promote_options="default") if tables else pa.table({})
        if engine == "polars":
            import polars as pl

            return pl.from_arrow(table) if tables else pl.DataFrame()
        return table if engine == "arrow" else table.to_pandas()

    # ------------------------------------------------------------------ derived files

    def csv(
        self,
        key: str,
        *,
        tickers: str | Iterable[str] | None = None,
        dest: str | Path = "kalshieye-data",
        wait: float = 600.0,
    ) -> Path:
        """A gzipped CSV copy of one file from ``files()``, optionally cut to ``tickers``."""
        self._need_key()
        body = {"key": key, "tickers": [t.upper() for t in _as_list(tickers)]}
        out = self._poll(lambda: self._request("POST", "/api/v1/files/csv", body=body), wait=wait)
        if out.get("status") != "ready":
            raise ApiError(504, "CSV is still being built; call again later.")
        f = out["file"]
        rel = f["path"][: -len(".parquet")] if f["path"].endswith(".parquet") else f["path"]
        suffix = ".tickers.csv.gz" if body["tickers"] else ".csv.gz"
        return self._save(f, Path(dest) / (rel + suffix))

    def bundles(self, event: str, *, dest: str | Path = "kalshieye-bundles", wait: float = 900.0) -> list[Path]:
        """Every zip of one Kalshi event: book_l2, book_snap_1s, bbo, trades, spot (what your plan
        includes), plus metadata.json and settlement.json. A zip holds up to 31 published days; an
        event with more comes as several, each named by its days."""
        self._need_key()
        eid = event.strip().upper()
        path = f"/api/v1/events/{urllib.parse.quote(eid)}/bundle"
        saved: list[Path] = []
        start: str | None = None
        while True:
            params = {"start_date": start} if start else None
            out = self._poll(lambda: self._request("POST", path, params=params), wait=wait)
            if out.get("status") != "ready":
                raise ApiError(504, "Bundle is still being built; call again later.")
            saved.append(self._save(out["file"], Path(dest) / out["file"]["filename"]))
            start = out.get("next_start_date")
            if not start:
                return saved

    def bundle(self, event: str, *, dest: str | Path = "kalshieye-bundles", wait: float = 900.0) -> Path:
        """The zip of one Kalshi event, when it fits in one (31 published days); otherwise this raises
        and ``bundles()`` saves them all."""
        self._need_key()
        eid = event.strip().upper()
        out = self._poll(lambda: self._request("POST", f"/api/v1/events/{urllib.parse.quote(eid)}/bundle"), wait=wait)
        if out.get("status") != "ready":
            raise ApiError(504, "Bundle is still being built; call again later.")
        if out.get("next_start_date"):
            raise ApiError(413, f"{eid} spans {out.get('days_total')} published days, more than one zip holds: use bundles().")
        return self._save(out["file"], Path(dest) / out["file"]["filename"])

    def replay(self, ticker: str, *, date: str | None = None, wait: float = 300.0) -> dict[str, Any]:
        """Every top-10 book frame (one a second the book changed) and every trade of one
        market-day: the API pages them (up to 3600 frames a page) and this walks every page."""
        self._need_key()
        path = f"/api/v1/markets/{urllib.parse.quote(ticker.strip().upper())}/replay"
        out: dict[str, Any] | None = None
        offset: int | None = 0
        while offset is not None:
            params: dict[str, Any] = {"offset": offset}
            if date:
                params["date"] = date
            page = self._poll(lambda: self._request("GET", path, params=params), wait=wait)
            if page.get("status") != "ready":
                raise ApiError(504, "Replay is still being prepared; call again later.")
            if out is None:
                out = page
            else:
                out["frames"] = out["frames"] + page["frames"]
                out["trades"] = out["trades"] + page["trades"]
            offset = page.get("next_offset")
        assert out is not None
        out["next_offset"] = None
        got = (len(out["frames"]), len(out["trades"]))
        want = (out.get("frames_total", got[0]), out.get("trades_total", got[1]))
        if got != want:
            raise ApiError(502, f"Replay pages held {got[0]} frames and {got[1]} trades, not {want[0]} and {want[1]}; call again.")
        return out

    def create_s3_key(self, name: str = "default") -> dict[str, Any]:
        """Create a read-only S3 key for syncing whole datasets (paid plans). S3 sync is coming soon:
        until it's switched on, the API answers 503.

        Returns endpoint, bucket, access_key_id, secret_access_key (shown once), and
        ready-to-paste snippets for aws, rclone, DuckDB, and boto3.
        """
        self._need_key()
        return self._request("POST", "/api/v1/files/keys", body={"name": name})

    def s3_keys(self) -> dict[str, Any]:
        """Your S3 keys, with last use and data counted per key."""
        self._need_key()
        return self._request("GET", "/api/v1/files/keys")

    def revoke_s3_key(self, key_id: str) -> dict[str, Any]:
        self._need_key()
        return self._request("DELETE", f"/api/v1/files/keys/{urllib.parse.quote(key_id)}")

    # ------------------------------------------------------------------ saved requests

    def save_request(
        self,
        datasets: str | Sequence[str],
        start_date: str,
        end_date: str,
        *,
        series: str | Iterable[str] | None = None,
        categories: str | Iterable[str] | None = None,
        tickers: str | Iterable[str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        self._need_key()
        body = self._selection(datasets, start_date, end_date, series, categories, tickers)
        if name:
            body["name"] = name
        return self._request("POST", "/api/v1/requests", body=body)

    def requests(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Your saved requests, newest first: every one, or the newest ``limit``."""
        self._need_key()
        out: list[dict[str, Any]] = []
        offset: int | None = 0
        while offset is not None and (limit is None or len(out) < limit):
            size = 100 if limit is None else max(1, min(100, limit - len(out)))
            page = self._request("GET", "/api/v1/requests", params={"limit": size, "offset": offset})
            out += page["requests"]
            offset = page.get("next_offset")
        return out
