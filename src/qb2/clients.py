from __future__ import annotations

from typing import Dict, List

import oci
from oci import config as oci_config
from oci.bastion import BastionClient
from oci.core import ComputeClient, VirtualNetworkClient
from oci.database import DatabaseClient
from oci.identity import IdentityClient
from oci.resource_search import ResourceSearchClient
from oci.compute_instance_agent import PluginClient
from oci.exceptions import ConfigFileNotFound, InvalidConfig

from .types import OciClients
from .util import error_exit


def load_profile(profile: str) -> dict:
    try:
        cfg = oci_config.from_file(profile_name=profile)
    except (ConfigFileNotFound, InvalidConfig, KeyError) as e:
        error_exit(
            f"OCI configuration not found or invalid for profile '{profile}'. "
            "Ensure ~/.oci/config exists and includes required keys (tenancy, user, fingerprint, key_file, region). "
            "You can create it with 'oci setup config'. Error: " + str(e)
        )
    # Validate required keys explicitly for clearer error before client init
    required = ["tenancy", "user", "fingerprint", "key_file", "region"]
    missing = [k for k in required if k not in cfg or not cfg.get(k)]
    if missing:
        error_exit(
            "OCI config profile is missing required keys: " + ", ".join(missing) +
            ". Update ~/.oci/config or select the correct profile with --profile."
        )
    return cfg


def build_clients(cfg: dict, timeout: float | None = None) -> OciClients:
    kwargs = {"timeout": timeout} if timeout else {}
    return OciClients(
        bastion_client=BastionClient(cfg, **kwargs),
        compute_client=ComputeClient(cfg, **kwargs),
        vcn_client=VirtualNetworkClient(cfg, **kwargs),
        search_client=ResourceSearchClient(cfg, **kwargs),
        plugin_client=PluginClient(cfg, **kwargs),
        database_client=DatabaseClient(cfg, **kwargs),
        identity_client=IdentityClient(cfg, **kwargs),
        cfg=cfg,
    )


def list_compartments(identity_client: IdentityClient, tenancy_id: str, include_children: bool = True) -> List[Dict]:
    # Always include root compartment (tenancy)
    root = identity_client.get_compartment(tenancy_id).data
    results = [
        {
            "id": root.id,
            "name": root.name,
            "description": getattr(root, "description", ""),
            "compartment_id": root.compartment_id,
            "lifecycle_state": root.lifecycle_state,
        }
    ]

    if not include_children:
        return results

    # List child compartments; include only ACTIVE by default
    children = oci.pagination.list_call_get_all_results(
        identity_client.list_compartments,
        tenancy_id,
        compartment_id_in_subtree=True,
        access_level="ANY",
    ).data

    for c in children:
        if getattr(c, "lifecycle_state", None) == "ACTIVE":
            results.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "description": getattr(c, "description", ""),
                    "compartment_id": c.compartment_id,
                    "lifecycle_state": c.lifecycle_state,
                }
            )
    return results
