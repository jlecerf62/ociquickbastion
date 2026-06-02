import sys
from types import SimpleNamespace

fake_oci = SimpleNamespace(
    bastion=SimpleNamespace(
        models=SimpleNamespace(
            UpdateBastionDetails=lambda **kwargs: SimpleNamespace(**kwargs),
        )
    ),
    wait_until=lambda *args, **kwargs: None,
    pagination=SimpleNamespace(list_call_get_all_results=lambda *args, **kwargs: SimpleNamespace(data=[])),
)
sys.modules.setdefault("oci", fake_oci)

from qb2.bastion import BastionManager
from qb2.types import AllowListMode


class _FakeBastionClient:
    def __init__(self):
        self._cidrs = ["0.0.0.0/0"]
        self.update_calls = []

    def get_bastion(self, bastion_id):
        return SimpleNamespace(data=SimpleNamespace(client_cidr_block_allow_list=list(self._cidrs), lifecycle_state="ACTIVE", id=bastion_id))

    def update_bastion(self, bastion_id, details):
        self._cidrs = list(details.client_cidr_block_allow_list)
        self.update_calls.append((bastion_id, list(self._cidrs)))
        return SimpleNamespace(data=SimpleNamespace(id=bastion_id))


class _FakeClients:
    def __init__(self):
        self.bastion_client = _FakeBastionClient()


def test_reconcile_allow_list_merge_adds_missing_ip():
    manager = BastionManager(_FakeClients())
    out = manager.reconcile_allow_list("b1", "1.2.3.4/32", AllowListMode.MERGE)
    assert out == ["0.0.0.0/0", "1.2.3.4/32"]


def test_reconcile_allow_list_strict_replaces_existing():
    manager = BastionManager(_FakeClients())
    out = manager.reconcile_allow_list("b1", "1.2.3.4/32", AllowListMode.STRICT)
    assert out == ["1.2.3.4/32"]


def test_update_allow_list_uses_client_call(monkeypatch):
    clients = _FakeClients()
    manager = BastionManager(clients)

    monkeypatch.setattr("qb2.bastion.oci.wait_until", lambda *args, **kwargs: None)
    manager.update_allow_list("b1", ["9.9.9.9/32"])
    assert clients.bastion_client.update_calls == [("b1", ["9.9.9.9/32"])]
