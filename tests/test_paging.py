"""replay(), bundles()/bundle() and requests() walk every page the API hands out (0.2.4)."""
from pathlib import Path

import pytest

from kalshieye import ApiError, Client


class _Paged(Client):
    """The client over canned API answers: what it asks for and in what order."""

    def __init__(self, answers) -> None:
        super().__init__(api_key="clab_test", base_url="http://api.test")
        self.poll_interval = 0
        self.answers = answers
        self.asked: list[tuple[str, str, dict]] = []
        self.saved: list[str] = []

    def _request(self, method, path, *, params=None, body=None):
        self.asked.append((method, path, dict(params or {})))
        return self.answers(method, path, dict(params or {}))

    def _save(self, f, dest):
        self.saved.append(str(dest.name))
        return dest


def test_replay_walks_every_page() -> None:
    """Oct 27, 2026: replay() returned the first page (3,600 frames) as the whole market-day."""
    frames = [{"ts": i} for i in range(7)]
    trades = [{"ts": i} for i in range(0, 7, 2)]

    def answers(method, path, params):
        o = int(params["offset"])
        page = frames[o:o + 3]
        return {"status": "ready", "frames": page, "trades": [t for t in trades if page and page[0]["ts"] <= t["ts"] <= page[-1]["ts"]],
                "frames_total": 7, "trades_total": 4, "next_offset": o + 3 if o + 3 < 7 else None}

    kx = _Paged(answers)
    out = kx.replay("KXA-1", date="2026-10-26")
    assert out["frames"] == frames and out["trades"] == trades and out["next_offset"] is None
    assert [p["offset"] for _m, _p, p in kx.asked] == [0, 3, 6] and all(p["date"] == "2026-10-26" for _m, _p, p in kx.asked)


def test_bundles_save_every_zip_and_bundle_refuses_a_partial_one(tmp_path: Path) -> None:
    chunks = {None: ("2026-10-12", "2026-11-11", "2026-11-12"), "2026-11-12": ("2026-11-12", "2026-11-20", None)}

    def answers(method, path, params):
        first, last, nxt = chunks[params.get("start_date")]
        return {"status": "ready", "days_total": 40, "next_start_date": nxt,
                "file": {"url": "u", "filename": f"kalshieye-KXE-1-{first}-to-{last}.zip"}}

    kx = _Paged(answers)
    paths = kx.bundles("kxe-1", dest=tmp_path)
    assert [p.name for p in paths] == ["kalshieye-KXE-1-2026-10-12-to-2026-11-11.zip", "kalshieye-KXE-1-2026-11-12-to-2026-11-20.zip"]
    with pytest.raises(ApiError) as exc:
        _Paged(answers).bundle("KXE-1", dest=tmp_path)
    assert exc.value.status == 413 and "bundles()" in str(exc.value)


def test_saved_requests_walk_every_page() -> None:
    made = [{"id": f"r{i}"} for i in range(250)]

    def answers(method, path, params):
        o, n = int(params["offset"]), int(params["limit"])
        return {"requests": made[o:o + n], "next_offset": o + n if o + n < len(made) else None}

    assert _Paged(answers).requests() == made
    assert _Paged(answers).requests(limit=120) == made[:120]
