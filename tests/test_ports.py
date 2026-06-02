import pytest

from qb2.ssh import find_available_local_port, resolve_local_port


class _FakeSocket:
    def __init__(self, busy_ports):
        self._busy_ports = busy_ports

    def setsockopt(self, *_args, **_kwargs):
        return None

    def bind(self, addr):
        port = addr[1]
        if port in self._busy_ports:
            raise OSError("busy")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patch_socket(monkeypatch, busy_ports):
    monkeypatch.setattr(
        "qb2.ssh.socket.socket",
        lambda *_args, **_kwargs: _FakeSocket(set(busy_ports)),
    )


def test_find_available_local_port_prefers_preferred_when_free(monkeypatch):
    _patch_socket(monkeypatch, busy_ports={})
    chosen = find_available_local_port(4444, 4444, 4450)
    assert chosen == 4444


def test_find_available_local_port_skips_busy_preferred(monkeypatch):
    _patch_socket(monkeypatch, busy_ports={4444, 4445})
    chosen = find_available_local_port(4444, 4444, 4447)
    assert chosen == 4446


def test_find_available_local_port_raises_when_range_full(monkeypatch):
    _patch_socket(monkeypatch, busy_ports={5000, 5001, 5002})
    with pytest.raises(RuntimeError):
        find_available_local_port(5000, 5000, 5002)


def test_resolve_local_port_accepts_auto(monkeypatch):
    _patch_socket(monkeypatch, busy_ports={3128})
    port = resolve_local_port("SOCKS", "auto", 3128)
    assert port == 1024


def test_resolve_local_port_rejects_invalid_value():
    with pytest.raises(ValueError):
        resolve_local_port("PFWD", "abc", 4444)
