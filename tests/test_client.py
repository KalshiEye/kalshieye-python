import pytest

from kalshieye import ApiError, Client


def test_a_missing_key_says_where_to_get_one(monkeypatch):
    monkeypatch.delenv("KALSHIEYE_API_KEY", raising=False)
    kx = Client(api_key=None)
    with pytest.raises(ApiError) as err:
        kx._need_key()
    assert err.value.status == 401 and "https://kalshieye.com/app/api/" in str(err.value)
