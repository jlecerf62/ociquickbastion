from qb2 import prompts
from qb2.types import TargetPrivateIp


def _private_ip(ip_address: str, name: str) -> TargetPrivateIp:
    return TargetPrivateIp(
        ip_address=ip_address,
        vnic_id=f"vnic-{name}",
        vnic_name=name,
        subnet_id=f"subnet-{name}",
        vcn_id=f"vcn-{name}",
        display_name=f"{name}-ip",
        is_primary=name == "primary",
    )


def test_choose_private_ip_maps_selected_label(monkeypatch):
    choices = [_private_ip("10.0.0.10", "primary"), _private_ip("10.0.1.10", "secondary")]

    monkeypatch.setattr(
        prompts,
        "_choose_from_list",
        lambda options, title: options[1],
    )

    assert prompts.choose_private_ip(choices) is choices[1]


def test_choose_private_ip_returns_only_choice_without_prompt(monkeypatch):
    choices = [_private_ip("10.0.0.10", "primary")]

    def fail_choose(*_args, **_kwargs):
        raise AssertionError("selector should not be shown for a single IP")

    monkeypatch.setattr(prompts, "_choose_from_list", fail_choose)

    assert prompts.choose_private_ip(choices) is choices[0]
