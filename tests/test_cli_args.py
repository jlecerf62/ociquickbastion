import sys
import types
from argparse import Namespace

sys.modules.setdefault("oci", types.SimpleNamespace())
from qb2.cli import (
    _apply_target_private_ip,
    _effective_instance_ip,
    _is_direct_pfwd_request,
    _should_offer_private_ip_selector,
    parse_args,
)
from qb2.types import ResourceType, SessionMode, TargetPrivateIp, TargetResource


def test_parse_args_local_port_auto(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["qb", "--local-port", "auto"])
    args = parse_args()
    assert args.local_port == "auto"


def test_parse_args_pfwd_ip_and_ports(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["qb", "-i", "10.0.1.25", "-r", "1521", "-l", "11521"],
    )
    args = parse_args()
    assert args.instance_ip == "10.0.1.25"
    assert args.remote_port == 1521
    assert args.local_port == "11521"


def test_parse_args_allowlist_and_recent(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["qb", "--allowlist", "current-ip", "--allowlist-mode", "strict", "--recent"],
    )
    args = parse_args()
    assert args.allowlist == "current-ip"
    assert args.allowlist_mode == "strict"
    assert args.recent is True


def test_parse_args_tui(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["qb", "--tui"])
    args = parse_args()
    assert args.tui is True


def test_effective_instance_ip_uses_override_for_pfwd():
    assert (
        _effective_instance_ip(SessionMode.PFWD, "10.0.1.25", "10.0.0.10")
        == "10.0.1.25"
    )


def test_effective_instance_ip_uses_target_ip_for_pfwd_without_override():
    assert _effective_instance_ip(SessionMode.PFWD, None, "10.0.0.10") == "10.0.0.10"


def test_effective_instance_ip_ignores_override_for_ssh():
    assert (
        _effective_instance_ip(SessionMode.SSH, "10.0.1.25", "10.0.0.10")
        == "10.0.0.10"
    )


def test_effective_instance_ip_returns_none_for_socks():
    assert _effective_instance_ip(SessionMode.SOCKS, "10.0.1.25", "10.0.0.10") is None


def test_direct_pfwd_request_detects_ip_and_local_port():
    args = Namespace(mode=None, instance_ip="10.235.108.49", local_port="4445")
    assert _is_direct_pfwd_request(args) is True


def test_direct_pfwd_request_requires_local_port():
    args = Namespace(mode=None, instance_ip="10.235.108.49", local_port=None)
    assert _is_direct_pfwd_request(args) is False


def test_direct_pfwd_request_rejects_non_pfwd_mode():
    args = Namespace(mode="SSH", instance_ip="10.235.108.49", local_port="4445")
    assert _is_direct_pfwd_request(args) is False


def test_should_offer_private_ip_selector_for_interactive_compute_pfwd():
    args = Namespace(auto=False, instance_ip=None)
    target = TargetResource(
        ocid="instance-1",
        name="instance",
        resource_type=ResourceType.COMPUTE,
        private_ip="10.0.0.10",
        compartment_id="compartment-1",
        compartment_name="compartment",
        vcn_id="vcn-1",
        subnet_id="subnet-1",
        region="eu-paris-1",
        state="RUNNING",
    )

    assert _should_offer_private_ip_selector(args, SessionMode.PFWD, target, False) is True


def test_should_not_offer_private_ip_selector_when_instance_ip_is_supplied():
    args = Namespace(auto=False, instance_ip="10.0.0.20")
    target = TargetResource(
        ocid="instance-1",
        name="instance",
        resource_type=ResourceType.COMPUTE,
        private_ip="10.0.0.10",
        compartment_id="compartment-1",
        compartment_name="compartment",
        vcn_id="vcn-1",
        subnet_id="subnet-1",
        region="eu-paris-1",
        state="RUNNING",
    )

    assert _should_offer_private_ip_selector(args, SessionMode.PFWD, target, False) is False


def test_apply_target_private_ip_updates_target_network_context():
    target = TargetResource(
        ocid="instance-1",
        name="instance",
        resource_type=ResourceType.COMPUTE,
        private_ip="10.0.0.10",
        compartment_id="compartment-1",
        compartment_name="compartment",
        vcn_id="vcn-1",
        subnet_id="subnet-1",
        region="eu-paris-1",
        state="RUNNING",
    )
    selected_ip = TargetPrivateIp(
        ip_address="10.0.2.20",
        vnic_id="vnic-2",
        vnic_name="secondary-vnic",
        subnet_id="subnet-2",
        vcn_id="vcn-2",
        display_name="secondary-ip",
        is_primary=False,
    )

    assert _apply_target_private_ip(target, selected_ip) is target
    assert target.private_ip == "10.0.2.20"
    assert target.subnet_id == "subnet-2"
    assert target.vcn_id == "vcn-2"
