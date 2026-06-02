from qb2.prompts import choose_ports
from qb2.types import SessionMode


def test_choose_ports_pfwd_uses_provided_default(monkeypatch):
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    local_port, remote_port = choose_ports(SessionMode.PFWD, default_local_port=4555)
    assert local_port == 4555
    assert remote_port == 22


def test_choose_ports_socks_uses_3128_baseline(monkeypatch):
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    local_port, remote_port = choose_ports(SessionMode.SOCKS)
    assert local_port == 3128
    assert remote_port == 22
