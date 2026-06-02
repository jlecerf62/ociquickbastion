from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Tuple

import oci

from .types import OciClients, TargetPrivateIp, TargetResource, ResourceType
from .util import log


def _region_from_cfg(cfg: dict) -> str:
    return cfg.get("region", "")


def get_compute_private_ip_and_subnet(clients: OciClients, instance_id: str, compartment_id: str) -> Tuple[str, str, str]:
    attachments = oci.pagination.list_call_get_all_results(
        clients.compute_client.list_vnic_attachments,
        compartment_id=compartment_id,
        instance_id=instance_id,
    ).data
    if not attachments:
        raise RuntimeError("No VNIC attachments found for instance")
    vnic_id = attachments[0].vnic_id
    vnic = clients.vcn_client.get_vnic(vnic_id).data
    subnet = clients.vcn_client.get_subnet(vnic.subnet_id).data
    return vnic.private_ip, vnic.subnet_id, subnet.vcn_id


def list_compute_private_ips(clients: OciClients, instance_id: str, compartment_id: str) -> List[TargetPrivateIp]:
    attachments = oci.pagination.list_call_get_all_results(
        clients.compute_client.list_vnic_attachments,
        compartment_id=compartment_id,
        instance_id=instance_id,
    ).data

    choices: dict[tuple[str, str], TargetPrivateIp] = {}
    for attachment in attachments:
        vnic_id = getattr(attachment, "vnic_id", None)
        if not vnic_id:
            continue
        vnic = clients.vcn_client.get_vnic(vnic_id).data
        vnic_name = getattr(vnic, "display_name", "") or getattr(attachment, "display_name", "")
        subnet_id = getattr(vnic, "subnet_id", "")
        vcn_id = getattr(vnic, "vcn_id", "")
        private_ips = oci.pagination.list_call_get_all_results(
            clients.vcn_client.list_private_ips,
            vnic_id=vnic_id,
        ).data
        if not private_ips and getattr(vnic, "private_ip", None):
            private_ips = [
                SimpleNamespace(
                    ip_address=vnic.private_ip,
                    display_name="",
                    is_primary=True,
                )
            ]
        for private_ip in private_ips:
            ip_address = getattr(private_ip, "ip_address", None) or getattr(private_ip, "private_ip", None)
            if not ip_address:
                continue
            choice = TargetPrivateIp(
                ip_address=ip_address,
                vnic_id=vnic_id,
                vnic_name=vnic_name,
                subnet_id=getattr(private_ip, "subnet_id", None) or subnet_id,
                vcn_id=vcn_id,
                display_name=getattr(private_ip, "display_name", "") or "",
                is_primary=bool(getattr(private_ip, "is_primary", False)),
            )
            choices[(vnic_id, ip_address)] = choice

    return sorted(
        choices.values(),
        key=lambda choice: (
            not choice.is_primary,
            choice.vnic_name.casefold(),
            choice.vnic_name,
            choice.ip_address,
        ),
    )


def list_compute_targets(
    clients: OciClients,
    compartment_id: str,
    compartment_name: str,
    include_states: set[str] | None = None,
    debug: bool = False,
) -> List[TargetResource]:
    region = _region_from_cfg(clients.cfg)
    items: List[TargetResource] = []

    if include_states is None or not include_states:
        include_states = {"RUNNING", "STOPPED"}

    try:
        instances: List[Any] = oci.pagination.list_call_get_all_results(
            clients.compute_client.list_instances,
            compartment_id,
        ).data
    except Exception as e:
        if debug:
            log("DEBUG", f"compute.list_instances failed in {compartment_name}: {e}")
        return items

    if debug:
        log("DEBUG", f"compute.list_instances count in {compartment_name}: {len(instances)}")

    for inst in instances:
        state = getattr(inst, "lifecycle_state", "")
        if state not in include_states:
            continue
        try:
            ip, subnet_id, vcn_id = get_compute_private_ip_and_subnet(clients, inst.id, inst.compartment_id)
        except Exception as e:
            if debug:
                log("DEBUG", f"get_compute_private_ip_and_subnet failed for {inst.id}: {e}")
            continue
        name = getattr(inst, "display_name", inst.id)
        items.append(
            TargetResource(
                ocid=inst.id,
                name=name,
                resource_type=ResourceType.COMPUTE,
                private_ip=ip,
                compartment_id=inst.compartment_id,
                compartment_name=compartment_name,
                vcn_id=vcn_id,
                subnet_id=subnet_id,
                region=region,
                state=state,
            )
        )
    return items


def list_dbnode_targets(
    clients: OciClients,
    compartment_id: str,
    compartment_name: str,
    include_states: set[str] | None = None,
    debug: bool = False,
) -> List[TargetResource]:
    region = _region_from_cfg(clients.cfg)
    items: List[TargetResource] = []

    if include_states is None or not include_states:
        include_states = {"AVAILABLE", "STOPPED"}

    nodes: List[Any] = []

    # DB Systems -> DbNodes
    try:
        systems = oci.pagination.list_call_get_all_results(
            clients.database_client.list_db_systems,
            compartment_id=compartment_id,
        ).data
    except Exception as e:
        if debug:
            log("DEBUG", f"database.list_db_systems failed in {compartment_name}: {e}")
        systems = []
    for sys in systems:
        try:
            sys_nodes = oci.pagination.list_call_get_all_results(
                clients.database_client.list_db_nodes,
                compartment_id=compartment_id,
                db_system_id=sys.id,
            ).data
            nodes.extend(sys_nodes)
        except Exception as e:
            if debug:
                name = getattr(sys, "display_name", sys.id)
                log("DEBUG", f"database.list_db_nodes (by dbSystemId) failed for {name}: {e}")

    # Exadata VM Clusters (Cloud Service) -> DbNodes
    try:
        vm_clusters = oci.pagination.list_call_get_all_results(
            clients.database_client.list_vm_clusters,
            compartment_id=compartment_id,
        ).data
    except Exception as e:
        if debug:
            log("DEBUG", f"database.list_vm_clusters failed in {compartment_name}: {e}")
        vm_clusters = []
    for vmc in vm_clusters:
        try:
            vmc_nodes = oci.pagination.list_call_get_all_results(
                clients.database_client.list_db_nodes,
                compartment_id=compartment_id,
                vm_cluster_id=vmc.id,
            ).data
            nodes.extend(vmc_nodes)
        except Exception as e:
            if debug:
                name = getattr(vmc, "display_name", vmc.id)
                log("DEBUG", f"database.list_db_nodes (by vmClusterId) failed for {name}: {e}")

    # Exadata Cloud@Customer VM Clusters -> DbNodes (best-effort)
    try:
        cvc_list = oci.pagination.list_call_get_all_results(
            clients.database_client.list_cloud_vm_clusters,
            compartment_id=compartment_id,
        ).data
    except Exception as e:
        if debug:
            log("DEBUG", f"database.list_cloud_vm_clusters failed in {compartment_name}: {e}")
        cvc_list = []
    for cvc in cvc_list:
        try:
            cvc_nodes = oci.pagination.list_call_get_all_results(
                clients.database_client.list_db_nodes,
                compartment_id=compartment_id,
                vm_cluster_id=cvc.id,
            ).data
            nodes.extend(cvc_nodes)
        except Exception as e:
            if debug:
                name = getattr(cvc, "display_name", cvc.id)
                log("DEBUG", f"database.list_db_nodes (by cloud vmClusterId) failed for {name}: {e}")

    if debug:
        log("DEBUG", f"database.list_db_nodes total in {compartment_name}: {len(nodes)}")

    for node in nodes:
        state = getattr(node, "lifecycle_state", "")
        if state not in include_states:
            continue
        name = getattr(node, "hostname", getattr(node, "display_name", node.id))
        ip = ""
        subnet_id = ""
        vcn_id = ""
        # Prefer direct vnic_id if available
        vnic_id = getattr(node, "vnic_id", None) or getattr(node, "vnicId", None)
        if vnic_id:
            try:
                vnic = clients.vcn_client.get_vnic(vnic_id).data
                ip = vnic.private_ip
                subnet_id = vnic.subnet_id
                vcn_id = vnic.vcn_id
            except Exception as e:
                if debug:
                    log("DEBUG", f"get_vnic failed for DbNode {node.id} vnic={vnic_id}: {e}")
                pass
        # Fallback via resource search on hostname to find private IP
        if not ip and name:
            try:
                ip = _private_ip_from_search(clients, name)
                if ip:
                    subnet_id = resolve_subnet_from_private_ip(clients, ip)
                    # Fetch subnet to get vcn_id
                    subnet = clients.vcn_client.get_subnet(subnet_id).data
                    vcn_id = subnet.vcn_id
            except Exception:
                pass
        if not ip:
            continue
        items.append(
            TargetResource(
                ocid=node.id,
                name=name,
                resource_type=ResourceType.DBNODE,
                private_ip=ip,
                compartment_id=compartment_id,
                compartment_name=compartment_name,
                vcn_id=vcn_id,
                subnet_id=subnet_id,
                region=region,
                state=state,
                default_os_user="opc",
            )
        )
    return items


def _private_ip_from_search(clients: OciClients, text: str) -> str:
    details = oci.resource_search.models.FreeTextSearchDetails(text=text)
    results = clients.search_client.search_resources(details).data
    # Attempt to find something that looks like an IP address in identifiers/strings
    import re

    ip_regex = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    for item in (results.items or []):
        # Check freeform text or identifier fields
        for attr in (getattr(item, "identifier", ""), getattr(item, "display_name", "")):
            m = ip_regex.search(attr or "")
            if m:
                return m.group(0)
    return ""


def resolve_subnet_from_private_ip(clients: OciClients, ip: str) -> str:
    details = oci.resource_search.models.FreeTextSearchDetails(text=ip)
    results = clients.search_client.search_resources(details).data
    private_ip_id = None
    for item in results.items or []:
        if getattr(item, "identifier", "").startswith("ocid1.privateip"):
            private_ip_id = item.identifier
            break
    if not private_ip_id:
        raise RuntimeError("Failed to find private IP via Resource Search")
    private_ip = clients.vcn_client.get_private_ip(private_ip_id).data
    return private_ip.subnet_id
