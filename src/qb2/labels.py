from __future__ import annotations

from typing import Dict, List, Optional

from .types import TargetPrivateIp


def target_private_ip_label(private_ip: TargetPrivateIp) -> str:
    role = "primary" if private_ip.is_primary else "secondary"
    vnic = private_ip.vnic_name or private_ip.vnic_id
    parts = [private_ip.ip_address, role]
    if vnic:
        parts.append(f"VNIC {vnic}")
    if private_ip.display_name and private_ip.display_name != private_ip.ip_address:
        parts.append(private_ip.display_name)
    return " | ".join(parts)


def compartment_path_labels(compartments: List[dict]) -> Dict[str, str]:
    by_id = {c["id"]: c for c in compartments}
    labels: Dict[str, str] = {}

    def path_for(compartment: dict) -> str:
        names = [compartment["name"]]
        parent_id = compartment.get("compartment_id")
        seen = {compartment["id"]}
        while parent_id and parent_id in by_id and parent_id not in seen:
            parent = by_id[parent_id]
            names.append(parent["name"])
            seen.add(parent_id)
            parent_id = parent.get("compartment_id")
        path = list(reversed(names))
        if len(path) > 1:
            path = path[1:]
        return " / ".join(path)

    for compartment in compartments:
        labels[compartment["id"]] = path_for(compartment)

    counts: Dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1

    for compartment_id, label in list(labels.items()):
        if counts[label] > 1:
            labels[compartment_id] = f"{label} [{compartment_id[-12:]}]"

    return labels


def sort_compartments_by_hierarchy(compartments: List[dict]) -> List[dict]:
    ids = {c["id"] for c in compartments}
    children_by_parent: Dict[Optional[str], List[dict]] = {}
    roots: List[dict] = []

    for compartment in compartments:
        parent_id = compartment.get("compartment_id")
        if parent_id and parent_id in ids:
            children_by_parent.setdefault(parent_id, []).append(compartment)
        else:
            roots.append(compartment)

    def sort_key(compartment: dict) -> tuple:
        name = compartment.get("name", "")
        return (name.casefold(), name, compartment.get("id", ""))

    ordered: List[dict] = []
    seen = set()

    def visit(compartment: dict) -> None:
        compartment_id = compartment["id"]
        if compartment_id in seen:
            return
        seen.add(compartment_id)
        ordered.append(compartment)
        for child in sorted(children_by_parent.get(compartment_id, []), key=sort_key):
            visit(child)

    for root in sorted(roots, key=sort_key):
        visit(root)

    for compartment in sorted(compartments, key=sort_key):
        visit(compartment)

    return ordered
