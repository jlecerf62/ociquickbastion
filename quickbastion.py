#!/usr/bin/env python3
"""
Script: quickbastion.py
Description: Create and manage OCI Bastion sessions using only the OCI Python SDK,
             while loading credentials from the standard ~/.oci/config profile.

Parity with original bash script quickbastion.sh:
- Reads OCI config profile
- Ensures SSH keypair exists (prompts to generate with ssh-keygen if missing)
- Validates Bastion plugin RUNNING on instance for managed SSH mode
- Resolves subnet from instance OCID or private IP
- Finds or creates a Bastion in subnet or VCN's compartment
- Creates Bastion session: managed SSH, port-forwarding, or dynamic SOCKS5
- Prints final SSH command to connect, with optional HTTP(S) proxy support

Requirements:
- Python 3
- OCI Python SDK: pip install oci
- ssh-keygen available on PATH (for local RSA key generation)

Usage:
  python3 quickbastion.py [-h| -i IP | -s SUBNET_OCID] [-r REMOTE_PORT -l LOCAL_PORT] [-u USER] [-p PROFILE] [INSTANCE_OCID]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

try:
    import oci
    from oci import config as oci_config
    from oci.bastion import BastionClient
    from oci.core import ComputeClient, VirtualNetworkClient
    from oci.exceptions import ServiceError, ConfigFileNotFound, InvalidConfig
    from oci.resource_search import ResourceSearchClient
    from oci.resource_search.models import FreeTextSearchDetails
    from oci.compute_instance_agent import PluginClient
    from oci.pagination import list_call_get_all_results
    from oci.bastion.models import (
        CreateBastionDetails,
        CreateSessionDetails,
        PublicKeyDetails,
        CreateManagedSshSessionTargetResourceDetails,
        CreatePortForwardingSessionTargetResourceDetails,
        CreateDynamicPortForwardingSessionTargetResourceDetails,
    )
except Exception as e:
    print("ERROR: OCI Python SDK not available. Install it with: pip install oci", file=sys.stderr)
    raise


DEFAULT_SESSION_TTL = 10800
DEFAULT_TARGET_PORT = 22
DEFAULT_OS_USERNAME = "opc"
DEFAULT_PROFILE = "DEFAULT"
DEFAULT_ALLOW_LIST = ["0.0.0.0/0"]
DEFAULT_FQDN_SOCKS = "ENABLED"

SSH_PRIVATE_KEY_PATH = str(Path.home() / ".ssh" / "id_rsa")
SSH_PUBLIC_KEY_PATH = f"{SSH_PRIVATE_KEY_PATH}.pub"


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def error_exit(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def prompt_yes_no(question: str) -> bool:
    try:
        reply = input(f"{question} (Y/N) ").strip().lower()
        return reply.startswith("y")
    except EOFError:
        return False


def check_ssh_keys(private_key: str = SSH_PRIVATE_KEY_PATH, public_key: str = SSH_PUBLIC_KEY_PATH) -> None:
    log("INFO", "Searching for SSH key...")
    if not (Path(private_key).exists() and Path(public_key).exists()):
        log("WARNING", f"{private_key} not found")
        if prompt_yes_no("Do you want to generate a new RSA keypair?"):
            generate_ssh_keys(private_key)
        else:
            error_exit("SSH keys are required but not found")
    else:
        log("INFO", f"{private_key} found")


def generate_ssh_keys(private_key: str) -> None:
    log("INFO", "Generating new RSA keypair...")
    private_path = Path(private_key)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "rsa", "-f", str(private_path), "-N", ""],
            check=True,
        )
    except FileNotFoundError:
        error_exit("ssh-keygen is not installed or not in PATH")
    except subprocess.CalledProcessError:
        error_exit("Failed to generate SSH keys")
    log("SUCCESS", "RSA keypair generated successfully")


def verify_profile(profile: str) -> dict:
    try:
        cfg = oci_config.from_file(profile_name=profile)
        return cfg
    except (ConfigFileNotFound, InvalidConfig, KeyError):
        error_exit(f"Profile name cannot be found in {str(Path.home() / '.oci' / 'config')}")
    except Exception as e:
        error_exit(f"Failed to load OCI config profile '{profile}': {e}")
    return {}


def build_clients(cfg: dict) -> Tuple[BastionClient, ComputeClient, VirtualNetworkClient, ResourceSearchClient, PluginClient]:
    bastion_client = BastionClient(cfg)
    compute_client = ComputeClient(cfg)
    vcn_client = VirtualNetworkClient(cfg)
    search_client = ResourceSearchClient(cfg)
    plugin_client = PluginClient(cfg)
    return bastion_client, compute_client, vcn_client, search_client, plugin_client


def get_instance(comp_client: ComputeClient, instance_ocid: str):
    return comp_client.get_instance(instance_id=instance_ocid).data


def get_instance_vnic_and_subnet(
    comp_client: ComputeClient, vcn_client: VirtualNetworkClient, instance_ocid: str, compartment_id: str
) -> Tuple[str, str, str]:
    # list vnic attachments for the instance in its compartment
    attachments = list_call_get_all_results(
        comp_client.list_vnic_attachments, compartment_id=compartment_id, instance_id=instance_ocid
    ).data
    if not attachments:
        error_exit("Failed to get VNIC attachments for instance")
    vnic_id = attachments[0].vnic_id
    vnic = vcn_client.get_vnic(vnic_id).data
    return vnic.private_ip, vnic.subnet_id, vnic_id


def resolve_subnet_from_ip(search_client: ResourceSearchClient, vcn_client: VirtualNetworkClient, ip: str) -> str:
    details = FreeTextSearchDetails(text=ip)
    results = search_client.search_resources(details).data
    private_ip_id = None
    for item in results.items or []:
        if item.identifier and item.identifier.startswith("ocid1.privateip"):
            private_ip_id = item.identifier
            break
    if not private_ip_id:
        error_exit("Failed to find private IP in Resource Search results")
    private_ip = vcn_client.get_private_ip(private_ip_id).data
    return private_ip.subnet_id


def get_subnet_details(vcn_client: VirtualNetworkClient, subnet_id: str):
    return vcn_client.get_subnet(subnet_id).data


def check_bastion_plugin(plugin_client: PluginClient, instance_ocid: str, compartment_id: str) -> None:
    log("INFO", "Detecting Bastion plugin state...")
    try:
        # API: get_instance_agent_plugin requires instanceagent_id (instance OCID), plugin_name, compartment_id
        plugin = plugin_client.get_instance_agent_plugin(
            instanceagent_id=instance_ocid, compartment_id=compartment_id, plugin_name="Bastion"
        ).data
    except ServiceError as se:
        error_exit(f"Failed to check plugin status: {se}")
    status = getattr(plugin, "status", None)
    display_name = getattr(plugin, "display_name", "Bastion")
    if status != "RUNNING":
        error_exit(f"Bastion plugin is not enabled on instance. Status: {status}. Please enable and retry later.")
    log("SUCCESS", f"Bastion plugin is in RUNNING state on instance (plugin {display_name})")


def find_or_create_bastion(
    bastion_client: BastionClient,
    subnet_id: str,
    subnet_name: str,
    subnet_compartment_id: str,
    vcn_id: str,
    allow_list: list[str],
    dns_proxy_status: str,
) -> str:
    log("INFO", "Checking for existing Bastion service...")
    # Find existing ACTIVE bastion targeting the subnet
    bastions = list_call_get_all_results(
        bastion_client.list_bastions, compartment_id=subnet_compartment_id
    ).data

    def active_targeting_subnet(b):
        return getattr(b, "lifecycle_state", None) == "ACTIVE" and getattr(b, "target_subnet_id", None) == subnet_id

    def active_targeting_vcn(b):
        return getattr(b, "lifecycle_state", None) == "ACTIVE" and getattr(b, "target_vcn_id", None) == vcn_id

    bastion_id = None
    for b in bastions:
        if active_targeting_subnet(b):
            bastion_id = b.id
            break

    if not bastion_id:
        for b in bastions:
            if active_targeting_vcn(b):
                bastion_id = b.id
                break

    if not bastion_id:
        log("WARNING", "Bastion service not present for this subnet (in subnet compartment)")
        if not prompt_yes_no("Do you want to create it?"):
            error_exit("Bastion creation cancelled")
        details = CreateBastionDetails(
            bastion_type="STANDARD",
            compartment_id=subnet_compartment_id,
            target_subnet_id=subnet_id,
            name=f"QuickBastion{subnet_name}",
            client_cidr_block_allow_list=allow_list,
            dns_proxy_status=dns_proxy_status,
        )
        resp = bastion_client.create_bastion(details)
        # Wait until bastion becomes ACTIVE
        oci.wait_until(bastion_client, bastion_client.get_bastion(resp.data.id), "lifecycle_state", "ACTIVE", max_wait_seconds=180)
        bastion_id = resp.data.id
        log("SUCCESS", "Bastion service created successfully")
    else:
        log("SUCCESS", "Bastion service found")
    return bastion_id


def create_session(
    bastion_client: BastionClient,
    mode: str,
    bastion_id: str,
    ssh_public_key_path: str,
    session_ttl: int,
    target_port: int,
    instance_ip: Optional[str],
    instance_ocid: Optional[str],
    target_os_username: str,
) -> Tuple[str, str, str]:
    log("INFO", "Creating session... This may take up to 2 minutes...")

    try:
        ssh_public_key_content = Path(ssh_public_key_path).read_text()
    except Exception as e:
        error_exit(f"Failed to read SSH public key: {e}")

    key_details = PublicKeyDetails(public_key_content=ssh_public_key_content)

    if mode == "pfwd":
        if not instance_ip:
            error_exit("instance IP is required for port-forwarding mode")
        target_details = CreatePortForwardingSessionTargetResourceDetails(
            target_resource_private_ip_address=instance_ip,
            target_resource_port=target_port,
        )
        details = CreateSessionDetails(
            bastion_id=bastion_id,
            key_type="PUB",
            key_details=key_details,
            target_resource_details=target_details,
            session_ttl_in_seconds=session_ttl,
        )
    elif mode == "socks":
        target_details = CreateDynamicPortForwardingSessionTargetResourceDetails()
        details = CreateSessionDetails(
            bastion_id=bastion_id,
            key_type="PUB",
            key_details=key_details,
            target_resource_details=target_details,
            session_ttl_in_seconds=session_ttl,
        )
    else:
        if not instance_ocid:
            error_exit("instance OCID is required for managed SSH mode")
        target_details = CreateManagedSshSessionTargetResourceDetails(
            target_resource_id=instance_ocid,
            target_resource_private_ip_address=instance_ip,
            target_resource_port=target_port,
            target_resource_operating_system_user_name=target_os_username,
        )
        details = CreateSessionDetails(
            bastion_id=bastion_id,
            key_type="PUB",
            key_details=key_details,
            target_resource_details=target_details,
            session_ttl_in_seconds=session_ttl,
        )

    try:
        resp = bastion_client.create_session(details)
        session_id = resp.data.id
        # Wait until session ACTIVE
        oci.wait_until(bastion_client, bastion_client.get_session(session_id), "lifecycle_state", "ACTIVE", max_wait_seconds=180)
        sess = bastion_client.get_session(session_id).data
        bastion_user_name = getattr(sess, "bastion_user_name", None)
    except ServiceError as se:
        error_exit(f"Failed to create session: {se}")
    except Exception as e:
        error_exit(f"Failed to create session: {e}")

    # Derive region from session OCID: ocid1.bastionsession.oc1.REGION.xxxxx
    region = ""
    try:
        parts = session_id.split(".")
        if len(parts) >= 4:
            region = parts[3]
    except Exception:
        region = ""

    log("SUCCESS", f"Session has been created. Session lifetime is {session_ttl} seconds")
    print()
    print("Type the following SSH command to connect to bastion session:")
    print()

    return session_id, bastion_user_name or "", region


def build_ssh_command(
    mode: str,
    ssh_private_key: str,
    http_proxy: Optional[str],
    local_port: Optional[int],
    instance_ip: Optional[str],
    target_port: int,
    bastion_user_name: str,
    session_id: str,
    region: str,
) -> str:
    base_ssh_command = f"ssh -i {ssh_private_key}"

    # Helper to build a ProxyCommand using HTTP(S) proxy with nc
    def proxy_nc_opt():
        return f"-o 'ProxyCommand=nc -X connect -x {http_proxy} %h %p'" if http_proxy else ""

    if mode == "pfwd":
        if not (local_port and instance_ip):
            error_exit("local-port and instance-ip are required for port-forwarding mode")
        # ssh -i key -N -L local:instance_ip:target_port [proxy if present] -p 22 bastion_user@host.bastion.region.oci.oraclecloud.com
        cmd = f"{base_ssh_command} -N -L {local_port}:{instance_ip}:{target_port}"
        if http_proxy:
            cmd += f" {proxy_nc_opt()}"
        cmd += f" -p 22 {bastion_user_name}@host.bastion.{region}.oci.oraclecloud.com"
        return cmd

    if mode == "socks":
        if not local_port:
            error_exit("local-port is required for SOCKS mode")
        # ssh -i key -N -D 127.0.0.1:local_port [proxy if present] -p 22 bastion_user@host.bastion.region.oci.oraclecloud.com
        cmd = f"{base_ssh_command} -N -D 127.0.0.1:{local_port}"
        if http_proxy:
            cmd += f" {proxy_nc_opt()}"
        cmd += f" -p 22 {bastion_user_name}@host.bastion.{region}.oci.oraclecloud.com"
        return cmd

    # managed ssh:
    # Outer: ssh -i key -o ProxyCommand="ssh -i key [-o 'ProxyCommand=nc -X connect -x proxy %h %p'] -W %h:%p -p 22 session_id@host.bastion.region.oci.oraclecloud.com" -p 22 bastion_user@instance_ip
    if not instance_ip:
        error_exit("instance-ip is required for managed SSH mode")
    inner = f"ssh -i {ssh_private_key} "
    if http_proxy:
        inner += f"{proxy_nc_opt()} "
    inner += f"-W %h:%p -p 22 {session_id}@host.bastion.{region}.oci.oraclecloud.com"
    cmd = f'{base_ssh_command} -o ProxyCommand="{inner}" -p 22 {bastion_user_name}@{instance_ip}'
    return cmd


def get_http_proxy_from_env() -> Optional[str]:
    # Prefer http_proxy then https_proxy
    return os.environ.get("http_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create OCI BASTION session using OCI Python SDK.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-i", "--instance-ip", dest="instance_ip", help="Instance private IP")
    parser.add_argument("-r", "--remote-port", dest="remote_port", type=int, help="Remote TCP port (for port-forwarding)")
    parser.add_argument("-u", "--user", dest="user", default=DEFAULT_OS_USERNAME, help="Remote OS username")
    parser.add_argument("-p", "--profile", dest="profile", default=DEFAULT_PROFILE, help="OCI config profile name")
    parser.add_argument("-l", "--local-port", dest="local_port", type=int, help="Local TCP port (for port-forwarding or SOCKS)")
    parser.add_argument("-s", "--subnet-id", dest="subnet_id", help="Subnet OCID (SOCKS5 proxy mode)")
    parser.add_argument("--ttl", dest="session_ttl", type=int, default=DEFAULT_SESSION_TTL, help="Session TTL in seconds")
    parser.add_argument("--target-port", dest="target_port", type=int, default=DEFAULT_TARGET_PORT, help="Target port (managed ssh)")
    parser.add_argument("--ssh-private-key", dest="ssh_private_key", default=SSH_PRIVATE_KEY_PATH, help="Path to SSH private key")
    parser.add_argument("--ssh-public-key", dest="ssh_public_key", default=SSH_PUBLIC_KEY_PATH, help="Path to SSH public key")
    parser.add_argument("instance_ocid", nargs="?", help="Instance OCID")
    return parser.parse_args()


def main():
    args = parse_args()

    # Determine connection mode
    connection_mode = "ssh"
    if args.remote_port is not None:
        connection_mode = "pfwd"
    if args.subnet_id:
        connection_mode = "socks"  # overrides pfwd if both provided, mirroring bash behavior

    # Validate inputs
    if not any([args.instance_ocid, args.instance_ip, args.subnet_id]):
        error_exit("Either instance/subnet OCID or IP must be provided")

    # Check SSH keys (interactive if missing)
    check_ssh_keys(args.ssh_private_key, args.ssh_public_key)

    # Load profile config and init clients
    cfg = verify_profile(args.profile)
    bastion_client, compute_client, vcn_client, search_client, plugin_client = build_clients(cfg)

    # If managed SSH, validate Bastion plugin on the instance
    if connection_mode == "ssh":
        if not args.instance_ocid:
            error_exit("Managed SSH mode requires an instance OCID")
        inst = get_instance(compute_client, args.instance_ocid)
        check_bastion_plugin(plugin_client, args.instance_ocid, inst.compartment_id)

    # Resolve subnet_id if needed
    if not args.subnet_id:
        if args.instance_ip and not args.instance_ocid:
            # From IP => find privateIP OCID via resource search, then get subnet
            subnet_id = resolve_subnet_from_ip(search_client, vcn_client, args.instance_ip)
        else:
            if not args.instance_ocid:
                error_exit("Cannot resolve subnet without instance OCID or IP")
            inst = get_instance(compute_client, args.instance_ocid)
            _, subnet_id, _ = get_instance_vnic_and_subnet(compute_client, vcn_client, args.instance_ocid, inst.compartment_id)
    else:
        subnet_id = args.subnet_id

    # Subnet details
    subnet = get_subnet_details(vcn_client, subnet_id)
    subnet_name = subnet.display_name or ""
    subnet_compartment_id = subnet.compartment_id
    vcn_id = subnet.vcn_id

    # Find or create Bastion
    bastion_id = find_or_create_bastion(
        bastion_client=bastion_client,
        subnet_id=subnet_id,
        subnet_name=subnet_name,
        subnet_compartment_id=subnet_compartment_id,
        vcn_id=vcn_id,
        allow_list=DEFAULT_ALLOW_LIST,
        dns_proxy_status=DEFAULT_FQDN_SOCKS,
    )

    # Fill instance IP from instance OCID if needed (for managed and pfwd)
    instance_ip = args.instance_ip
    if args.instance_ocid and not instance_ip:
        inst = get_instance(compute_client, args.instance_ocid)
        ip, _, _ = get_instance_vnic_and_subnet(compute_client, vcn_client, args.instance_ocid, inst.compartment_id)
        instance_ip = ip

    # Create session
    session_id, bastion_user_name, region = create_session(
        bastion_client=bastion_client,
        mode=connection_mode,
        bastion_id=bastion_id,
        ssh_public_key_path=args.ssh_public_key,
        session_ttl=args.session_ttl,
        target_port=args.target_port if connection_mode != "pfwd" else args.remote_port or DEFAULT_TARGET_PORT,
        instance_ip=instance_ip,
        instance_ocid=args.instance_ocid,
        target_os_username=args.user,
    )

    # Build and print final SSH command
    http_proxy = get_http_proxy_from_env()
    ssh_cmd = build_ssh_command(
        mode=connection_mode,
        ssh_private_key=args.ssh_private_key,
        http_proxy=http_proxy,
        local_port=args.local_port,
        instance_ip=instance_ip,
        target_port=(args.remote_port or DEFAULT_TARGET_PORT) if connection_mode == "pfwd" else args.target_port,
        bastion_user_name=bastion_user_name,
        session_id=session_id,
        region=region,
    )
    print(ssh_cmd)
    print()

    # Extra note for SOCKS mode
    if connection_mode == "socks":
        print("Then you can connect to any resource in the subnet using IP or FQDN via SOCKS proxy, e.g.:")
        print()
        print(f"ssh -o 'ProxyCommand=nc -X connect -x 127.0.0.1:{args.local_port} %h %p' -p 22 <username>@<IP or FQDN>")
        print()


if __name__ == "__main__":
    main()
