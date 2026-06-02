from types import SimpleNamespace

from qb2 import discovery


class _FakeComputeClient:
    def __init__(self, attachments):
        self._attachments = attachments

    def list_vnic_attachments(self, **_kwargs):
        return self._attachments


class _FakeVcnClient:
    def __init__(self, vnics, private_ips):
        self._vnics = vnics
        self._private_ips = private_ips

    def get_vnic(self, vnic_id):
        return SimpleNamespace(data=self._vnics[vnic_id])

    def list_private_ips(self, vnic_id):
        return self._private_ips.get(vnic_id, [])


def _install_fake_pagination(monkeypatch):
    pagination = SimpleNamespace()
    monkeypatch.setattr(discovery.oci, "pagination", pagination, raising=False)
    monkeypatch.setattr(
        discovery.oci.pagination,
        "list_call_get_all_results",
        lambda fn, **kwargs: SimpleNamespace(data=fn(**kwargs)),
        raising=False,
    )


def _clients(attachments, vnics, private_ips):
    return SimpleNamespace(
        compute_client=_FakeComputeClient(attachments),
        vcn_client=_FakeVcnClient(vnics, private_ips),
    )


def test_list_compute_private_ips_returns_single_primary(monkeypatch):
    _install_fake_pagination(monkeypatch)
    clients = _clients(
        attachments=[SimpleNamespace(vnic_id="vnic-1")],
        vnics={
            "vnic-1": SimpleNamespace(
                display_name="primary-vnic",
                private_ip="10.0.0.10",
                subnet_id="subnet-1",
                vcn_id="vcn-1",
            )
        },
        private_ips={
            "vnic-1": [
                SimpleNamespace(
                    ip_address="10.0.0.10",
                    display_name="primary-ip",
                    is_primary=True,
                )
            ]
        },
    )

    choices = discovery.list_compute_private_ips(clients, "instance-1", "compartment-1")

    assert len(choices) == 1
    assert choices[0].ip_address == "10.0.0.10"
    assert choices[0].vnic_id == "vnic-1"
    assert choices[0].vnic_name == "primary-vnic"
    assert choices[0].subnet_id == "subnet-1"
    assert choices[0].vcn_id == "vcn-1"
    assert choices[0].display_name == "primary-ip"
    assert choices[0].is_primary is True


def test_list_compute_private_ips_includes_multiple_vnics_and_secondary_ips(monkeypatch):
    _install_fake_pagination(monkeypatch)
    clients = _clients(
        attachments=[SimpleNamespace(vnic_id="vnic-b"), SimpleNamespace(vnic_id="vnic-a")],
        vnics={
            "vnic-a": SimpleNamespace(
                display_name="app-vnic",
                private_ip="10.0.1.10",
                subnet_id="subnet-a",
                vcn_id="vcn-a",
            ),
            "vnic-b": SimpleNamespace(
                display_name="db-vnic",
                private_ip="10.0.2.10",
                subnet_id="subnet-b",
                vcn_id="vcn-b",
            ),
        },
        private_ips={
            "vnic-a": [
                SimpleNamespace(ip_address="10.0.1.11", display_name="app-secondary", is_primary=False),
                SimpleNamespace(ip_address="10.0.1.10", display_name="app-primary", is_primary=True),
            ],
            "vnic-b": [
                SimpleNamespace(ip_address="10.0.2.11", display_name="db-secondary", is_primary=False),
                SimpleNamespace(ip_address="10.0.2.10", display_name="db-primary", is_primary=True),
            ],
        },
    )

    choices = discovery.list_compute_private_ips(clients, "instance-1", "compartment-1")

    assert [choice.ip_address for choice in choices] == [
        "10.0.1.10",
        "10.0.2.10",
        "10.0.1.11",
        "10.0.2.11",
    ]
    assert [choice.subnet_id for choice in choices] == [
        "subnet-a",
        "subnet-b",
        "subnet-a",
        "subnet-b",
    ]
    assert [choice.vcn_id for choice in choices] == ["vcn-a", "vcn-b", "vcn-a", "vcn-b"]
    assert [choice.is_primary for choice in choices] == [True, True, False, False]


def test_list_compute_private_ips_falls_back_to_vnic_primary_ip(monkeypatch):
    _install_fake_pagination(monkeypatch)
    clients = _clients(
        attachments=[SimpleNamespace(vnic_id="vnic-1")],
        vnics={
            "vnic-1": SimpleNamespace(
                display_name="primary-vnic",
                private_ip="10.0.0.10",
                subnet_id="subnet-1",
                vcn_id="vcn-1",
            )
        },
        private_ips={"vnic-1": []},
    )

    choices = discovery.list_compute_private_ips(clients, "instance-1", "compartment-1")

    assert len(choices) == 1
    assert choices[0].ip_address == "10.0.0.10"
    assert choices[0].is_primary is True
