import io

import pytest

from qb2.net import resolve_current_public_ipv4


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_resolve_current_public_ipv4_returns_ip(monkeypatch):
    monkeypatch.setattr("qb2.net.urlopen", lambda *_args, **_kwargs: _Resp(b"1.2.3.4\n"))
    assert resolve_current_public_ipv4() == "1.2.3.4"


def test_resolve_current_public_ipv4_fallback(monkeypatch):
    calls = {"n": 0}

    def _fake_urlopen(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("boom")
        return _Resp(b"5.6.7.8")

    monkeypatch.setattr("qb2.net.urlopen", _fake_urlopen)
    assert resolve_current_public_ipv4() == "5.6.7.8"


def test_resolve_current_public_ipv4_raises_after_all_fail(monkeypatch):
    monkeypatch.setattr("qb2.net.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(RuntimeError):
        resolve_current_public_ipv4()
