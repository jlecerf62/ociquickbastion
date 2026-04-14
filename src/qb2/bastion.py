from __future__ import annotations

import oci
from typing import List, Optional, Tuple

from .types import OciClients, BastionSelection, SessionDescriptor, SessionMode
from .util import log


class BastionManager:
    def __init__(self, clients: OciClients) -> None:
        self.clients = clients

    def find_or_create_bastion(
        self,
        subnet_id: str,
        subnet_name: str,
        subnet_compartment_id: str,
        vcn_id: str,
        allow_list: list[str],
        dns_proxy_status: str,
        auto_create: bool = True,
    ) -> str:
        bastion_client = self.clients.bastion_client
        # List bastions in the subnet's compartment
        bastions = oci.pagination.list_call_get_all_results(
            bastion_client.list_bastions, compartment_id=subnet_compartment_id
        ).data

        def active_targeting_subnet(b):
            return getattr(b, "lifecycle_state", None) == "ACTIVE" and getattr(b, "target_subnet_id", None) == subnet_id

        def active_targeting_vcn(b):
            return getattr(b, "lifecycle_state", None) == "ACTIVE" and getattr(b, "target_vcn_id", None) == vcn_id

        bastion_id: Optional[str] = None
        for b in bastions:
            if active_targeting_subnet(b):
                bastion_id = b.id
                break
        if not bastion_id:
            for b in bastions:
                if active_targeting_vcn(b):
                    bastion_id = b.id
                    break

        if bastion_id:
            log("SUCCESS", "Bastion service found")
            return bastion_id

        if not auto_create:
            raise RuntimeError("No ACTIVE bastion found for subnet/VCN and auto_create=False")

        log("INFO", f"Creating Bastion QuickBastion{subnet_name} ... this may take up to 2 minutes")
        details = oci.bastion.models.CreateBastionDetails(
            bastion_type="STANDARD",
            compartment_id=subnet_compartment_id,
            target_subnet_id=subnet_id,
            name=f"QuickBastion{subnet_name}",
            client_cidr_block_allow_list=allow_list,
            dns_proxy_status=dns_proxy_status,
        )
        resp = bastion_client.create_bastion(details)
        oci.wait_until(bastion_client, bastion_client.get_bastion(resp.data.id), "lifecycle_state", "ACTIVE", max_wait_seconds=180)
        log("SUCCESS", "Bastion service created successfully")
        return resp.data.id

    def create_session(
        self,
        mode: SessionMode,
        bastion_id: str,
        ssh_public_key_path: str,
        session_ttl: int,
        target_port: int,
        instance_ip: Optional[str],
        instance_ocid: Optional[str],
        os_user: str,
    ) -> SessionDescriptor:
        bastion_client = self.clients.bastion_client
        key_details = oci.bastion.models.PublicKeyDetails(
            public_key_content=open(ssh_public_key_path, "r", encoding="utf-8").read()
        )

        if mode == SessionMode.PFWD:
            if not instance_ip:
                raise RuntimeError("instance IP is required for port-forwarding mode")
            target_details = oci.bastion.models.CreatePortForwardingSessionTargetResourceDetails(
                target_resource_private_ip_address=instance_ip,
                target_resource_port=target_port,
            )
        elif mode == SessionMode.SOCKS:
            target_details = oci.bastion.models.CreateDynamicPortForwardingSessionTargetResourceDetails()
        else:
            if not instance_ocid:
                raise RuntimeError("instance OCID is required for managed SSH mode")
            target_details = oci.bastion.models.CreateManagedSshSessionTargetResourceDetails(
                target_resource_id=instance_ocid,
                target_resource_private_ip_address=instance_ip,
                target_resource_port=target_port,
                target_resource_operating_system_user_name=os_user,
            )

        details = oci.bastion.models.CreateSessionDetails(
            bastion_id=bastion_id,
            key_type="PUB",
            key_details=key_details,
            target_resource_details=target_details,
            session_ttl_in_seconds=session_ttl,
        )

        resp = bastion_client.create_session(details)
        session_id = resp.data.id
        oci.wait_until(bastion_client, bastion_client.get_session(session_id), "lifecycle_state", "ACTIVE", max_wait_seconds=180)
        sess = bastion_client.get_session(session_id).data
        bastion_user_name = getattr(sess, "bastion_user_name", "")
        region = _region_from_session_id(session_id)
        target_id, target_ip, target_port_value, target_os_user = _extract_target_metadata(sess)
        if mode != SessionMode.SOCKS:
            # For PFWD and SSH the session should retain these details; fall back to inputs if absent.
            target_ip = target_ip or instance_ip
            target_port_value = target_port_value or target_port
        if mode == SessionMode.SSH:
            target_os_user = target_os_user or os_user
        log("SUCCESS", f"Session created. TTL {session_ttl} seconds")
        return SessionDescriptor(
            session_id=session_id,
            bastion_user_name=bastion_user_name,
            region=region,
            mode=mode,
            target_resource_id=target_id,
            target_private_ip=target_ip,
            target_port=target_port_value,
            target_os_user=target_os_user,
        )

    def list_active_sessions(self, bastion_id: str, mode: Optional[SessionMode] = None) -> List[SessionDescriptor]:
        bastion_client = self.clients.bastion_client
        sessions = oci.pagination.list_call_get_all_results(
            bastion_client.list_sessions, bastion_id=bastion_id
        ).data
        items: List[SessionDescriptor] = []
        for s in sessions:
            if getattr(s, "lifecycle_state", None) != "ACTIVE":
                continue
            # Fetch full session to ensure we have bastion_user_name
            try:
                full = bastion_client.get_session(s.id).data
            except Exception:
                full = s
            s_mode = _infer_mode_from_session(full)
            if mode and s_mode != mode:
                continue
            target_id, target_ip, target_port_value, target_os_user = _extract_target_metadata(full)
            items.append(
                SessionDescriptor(
                    session_id=s.id,
                    bastion_user_name=getattr(full, "bastion_user_name", ""),
                    region=_region_from_session_id(s.id),
                    mode=s_mode,
                    target_resource_id=target_id,
                    target_private_ip=target_ip,
                    target_port=target_port_value,
                    target_os_user=target_os_user,
                )
            )
        return items

    def get_session_details(self, session_id: str) -> SessionDescriptor:
        bastion_client = self.clients.bastion_client
        sess = bastion_client.get_session(session_id).data
        target_id, target_ip, target_port_value, target_os_user = _extract_target_metadata(sess)
        return SessionDescriptor(
            session_id=session_id,
            bastion_user_name=getattr(sess, "bastion_user_name", ""),
            region=_region_from_session_id(session_id),
            mode=_infer_mode_from_session(sess),
            target_resource_id=target_id,
            target_private_ip=target_ip,
            target_port=target_port_value,
            target_os_user=target_os_user,
        )


def _region_from_session_id(session_id: str) -> str:
    try:
        parts = session_id.split(".")
        if len(parts) >= 4:
            return parts[3]
    except Exception:
        pass
    return ""


def _infer_mode_from_session(sess) -> SessionMode:
    # Heuristic: check class name of target details
    target = getattr(sess, "target_resource_details", None)
    cname = target.__class__.__name__ if target else ""
    if "PortForwarding" in cname and "Dynamic" not in cname:
        return SessionMode.PFWD
    if "DynamicPortForwarding" in cname:
        return SessionMode.SOCKS
    return SessionMode.SSH


def _extract_target_metadata(sess) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    target = getattr(sess, "target_resource_details", None)
    if not target:
        return None, None, None, None
    target_id = getattr(target, "target_resource_id", None)
    target_ip = getattr(target, "target_resource_private_ip_address", None)
    target_port = getattr(target, "target_resource_port", None)
    target_os_user = getattr(target, "target_resource_operating_system_user_name", None)
    return target_id, target_ip, target_port, target_os_user
