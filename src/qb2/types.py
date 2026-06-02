from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class ResourceType(Enum):
    COMPUTE = "COMPUTE"
    DBNODE = "DBNODE"


class SessionMode(Enum):
    PFWD = "PFWD"  # Port-forwarding
    SSH = "SSH"    # Managed SSH
    SOCKS = "SOCKS"  # Dynamic port forwarding


class AllowListMode(Enum):
    MERGE = "merge"
    STRICT = "strict"
    PROMPT = "prompt"


@dataclass
class TargetResource:
    ocid: str
    name: str
    resource_type: ResourceType
    private_ip: str
    compartment_id: str
    compartment_name: str
    vcn_id: str
    subnet_id: str
    region: str
    state: str
    default_os_user: str = "opc"
    agent_status: Optional[str] = None  # e.g., RUNNING (for managed SSH)


@dataclass
class TargetPrivateIp:
    ip_address: str
    vnic_id: str
    vnic_name: str
    subnet_id: str
    vcn_id: str
    display_name: str
    is_primary: bool


@dataclass
class OciClients:
    bastion_client: "oci.bastion.BastionClient"
    compute_client: "oci.core.ComputeClient"
    vcn_client: "oci.core.VirtualNetworkClient"
    search_client: "oci.resource_search.ResourceSearchClient"
    plugin_client: "oci.compute_instance_agent.PluginClient"
    database_client: "oci.database.DatabaseClient"
    identity_client: "oci.identity.IdentityClient"
    cfg: dict


@dataclass
class BastionSelection:
    bastion_id: str
    subnet_id: str
    subnet_compartment_id: str
    subnet_name: str
    vcn_id: str


@dataclass
class SessionDescriptor:
    session_id: str
    bastion_user_name: str
    region: str
    mode: SessionMode
    target_resource_id: Optional[str] = None
    target_private_ip: Optional[str] = None
    target_port: Optional[int] = None
    target_os_user: Optional[str] = None


@dataclass
class LocalPortRequest:
    mode: SessionMode
    requested: Optional[int]
    auto: bool
    resolved: Optional[int]


@dataclass
class LocalSessionRecord:
    session_id: str
    target_ocid: Optional[str]
    target_name: str
    target_private_ip: Optional[str]
    mode: SessionMode
    region: str
    local_port: Optional[int]
    remote_port: Optional[int]
    os_user: Optional[str]
    created_at_utc: str
    last_used_at_utc: str
    status_hint: str
