from __future__ import annotations

import argparse
import os
import oci
from typing import List, Optional

from .config import (
    DEFAULT_ALLOW_LIST,
    DEFAULT_FQDN_SOCKS,
    DEFAULT_OS_USERNAME,
    DEFAULT_SESSION_TTL,
    DEFAULT_TARGET_PORT,
    SSH_PRIVATE_KEY_PATH,
    SSH_PUBLIC_KEY_PATH,
)
from .types import SessionMode, ResourceType, TargetResource, SessionDescriptor
from .util import log, error_exit


def parse_args():
    p = argparse.ArgumentParser(description="Interactive OCI Bastion helper (Compute & DB nodes)")
    p.add_argument("--profile", "-p", default="DEFAULT", help="OCI config profile name")
    p.add_argument("--compartment", help="Compartment OCID to limit discovery (default: all in current region)")
    p.add_argument("--region", help="Override region from profile (e.g., eu-paris-1)")
    p.add_argument("--mode", choices=["PFWD", "SSH", "SOCKS"], help="Session mode (default PFWD)")
    p.add_argument("--user", "-u", default=DEFAULT_OS_USERNAME, help="Remote OS username")
    p.add_argument("--remote-port", "-r", type=int, help="Remote/target port (PFWD/SSH; default 22)")
    p.add_argument("--local-port", "-l", type=int, help="Local port (PFWD/SOCKS)")
    p.add_argument("--resume", action="store_true", help="Try to resume an existing session if available")
    p.add_argument("--resume-index", type=int, help="Index (1-based) of session to resume when multiple exist")
    p.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    p.add_argument("--timeout", type=float, default=20.0, help="SDK connect/read timeout in seconds (default 20)")
    p.add_argument("--sdk-proxy", help="HTTP(S) proxy for OCI SDK requests, e.g. http://host:port")
    p.add_argument("--include-states-compute", help="Comma-separated lifecycle states to include for Compute (e.g., RUNNING,STOPPED,PROVISIONING)")
    p.add_argument("--auto", action="store_true", help="Auto-select first resource and default options (non-interactive)")
    return p.parse_args()


def _gather_resources(
    compartments: List[dict],
    selected_compartment: Optional[str],
    clients,
    include_states_compute: Optional[set[str]] = None,
    debug: bool = False,
) -> List[TargetResource]:
    # Local imports to avoid heavy OCI dependencies at import time
    from .discovery import list_compute_targets, list_dbnode_targets

    items: List[TargetResource] = []
    to_scan = compartments
    if selected_compartment:
        to_scan = [c for c in compartments if c["id"] == selected_compartment] or to_scan
    for c in to_scan:
        cname = c["name"]
        cid = c["id"]
        try:
            items.extend(list_compute_targets(clients, cid, cname, include_states=include_states_compute, debug=debug))
        except Exception:
            pass
        try:
            items.extend(list_dbnode_targets(clients, cid, cname, debug=debug))
        except Exception:
            pass
    return items


def _session_matches_target(session: SessionDescriptor, target: TargetResource) -> bool:
    if session.target_resource_id and session.target_resource_id == target.ocid:
        return True
    if session.target_private_ip and session.target_private_ip == target.private_ip:
        return True
    return False


def main():
    args = parse_args()

    # Local imports to avoid importing OCI SDK unless executing
    from .clients import load_profile, build_clients, list_compartments
    from .bastion import BastionManager
    from .ssh import ensure_ssh_keys, build_ssh_command, get_http_proxy_from_env, execute_ssh
    from .prompts import (
        choose_compartment,
        choose_target,
        choose_mode,
        choose_ports,
        choose_user,
        choose_resume_or_new,
    )

    # Prepare OCI clients
    # Optional proxy for SDK
    if args.sdk_proxy:
        os.environ["https_proxy"] = args.sdk_proxy
        os.environ["http_proxy"] = args.sdk_proxy
    cfg = load_profile(args.profile)
    if args.region:
        cfg["region"] = args.region
    clients = build_clients(cfg, timeout=args.timeout)

    # List compartments in tenancy (current region)
    tenancy_id = cfg.get("tenancy")
    if not tenancy_id:
        error_exit("Invalid OCI profile: missing tenancy OCID")
    if args.debug:
        proxy_val = os.environ.get('https_proxy') or os.environ.get('http_proxy') or ''
        log("DEBUG", f"Profile={args.profile}, Region={cfg.get('region')}, Tenancy={tenancy_id}")
        log("DEBUG", f"SDK timeout={args.timeout}s, proxies={proxy_val}")
    compartments = list_compartments(clients.identity_client, tenancy_id)

    # Choose compartment (optional)
    selected_compartment = args.compartment
    if not selected_compartment:
        selected_compartment = choose_compartment(compartments)
    # User pressed 'q' at compartment prompt -> quit
    if selected_compartment == "":
        error_exit("Operation cancelled by user")

    # Debug: which compartments will be scanned
    to_scan = compartments if not selected_compartment else [c for c in compartments if c["id"] == selected_compartment] or compartments
    if args.debug:
        names = [c["name"] for c in to_scan]
        log("DEBUG", f"Scanning {len(to_scan)} compartment(s): {', '.join(names)}")

    # Discover resources in current region (optionally limited by compartment)
    log("INFO", "Discovering resources in current region ...")
    include_states_compute: Optional[set[str]] = None
    if args.include_states_compute:
        include_states_compute = set(s.strip() for s in args.include_states_compute.split(",") if s.strip())
    resources = _gather_resources(compartments, selected_compartment, clients, include_states_compute=include_states_compute, debug=args.debug)
    if not resources:
        if selected_compartment:
            cname = next((c["name"] for c in compartments if c["id"] == selected_compartment), selected_compartment)
            error_exit(f"No resources found in compartment {cname} (check permissions/filters)")
        else:
            error_exit("No resources found in current region (check permissions/filters)")

    if args.debug:
        total = len(resources)
        comp = sum(1 for r in resources if r.resource_type == ResourceType.COMPUTE)
        dbn = sum(1 for r in resources if r.resource_type == ResourceType.DBNODE)
        log("DEBUG", f"Discovered resources: total={total}, compute={comp}, dbnode={dbn}")

    # Choose target
    if args.auto and resources:
        target = resources[0]
    else:
        target = choose_target(resources)
    if not target:
        error_exit("Operation cancelled by user")
    if len(resources) == 1:
        log("INFO", f"Selected only available target: {target.name} ({target.private_ip})")
    instance_ip = target.private_ip

    # Choose mode and ports/user
    user = args.user or DEFAULT_OS_USERNAME

    if args.auto:
        mode = SessionMode(args.mode) if args.mode else SessionMode.PFWD
        if mode == SessionMode.PFWD:
            local_port = args.local_port or 4444
            remote_port = args.remote_port or 22
        elif mode == SessionMode.SOCKS:
            local_port = args.local_port or 1080
            remote_port = 22
        else:
            local_port = None
            remote_port = args.remote_port or 22
    else:
        if args.resume:
            mode = SessionMode(args.mode) if args.mode else SessionMode.PFWD
            if mode == SessionMode.PFWD:
                local_port = args.local_port or 4444
                remote_port = args.remote_port or DEFAULT_TARGET_PORT
            elif mode == SessionMode.SOCKS:
                local_port = args.local_port or 1080
                remote_port = 22
            else:
                local_port = None
                remote_port = args.remote_port or 22
        else:
            mode = SessionMode(args.mode) if args.mode else choose_mode(SessionMode.PFWD)
            # Managed SSH allowed only for Compute instances
            if mode == SessionMode.SSH and target.resource_type != ResourceType.COMPUTE:
                log("WARNING", "Managed SSH not supported for DB nodes; switching to Port-forwarding")
                mode = SessionMode.PFWD
            local_port, remote_port = choose_ports(mode)
            # If user cancelled during port prompts
            if local_port is None and remote_port is None:
                error_exit("Operation cancelled by user")
            if remote_port is None:
                remote_port = DEFAULT_TARGET_PORT

    # Pre-check Managed SSH requirements: Bastion plugin
    if mode == SessionMode.SSH:
        plugin_status = None
        try:
            resp = clients.plugin_client.get_instance_agent_plugin(
                instanceagent_id=target.ocid,
                compartment_id=target.compartment_id,
                plugin_name="Bastion",
            )
            plugin_status = getattr(resp.data, "status", None)
        except Exception as e:
            if args.debug:
                log("DEBUG", f"Agent plugin status check failed: {e}")
        if plugin_status != "RUNNING":
            log("WARNING", f"Managed SSH requires Oracle Cloud Agent 'Bastion' plugin to be RUNNING on the instance, current status: {plugin_status or 'UNKNOWN/INACTIVE' }.")
            if args.auto:
                log("WARNING", "Falling back to Port-forwarding mode.")
                mode = SessionMode.PFWD
                # Set default ports when auto
                local_port = args.local_port or 4444
                remote_port = args.remote_port or 22
            else:
                ans = input("Switch to Port-forwarding instead? [Y/n]: ").strip().lower()
                if ans in ("", "y", "yes"):
                    mode = SessionMode.PFWD
                    local_port, remote_port = choose_ports(mode)
                    if local_port is None and remote_port is None:
                        error_exit("Operation cancelled by user")
                    if remote_port is None:
                        remote_port = DEFAULT_TARGET_PORT
                else:
                    error_exit("Managed SSH cannot proceed until the Bastion plugin is enabled on the instance.")

    # Ensure SSH keys
    ensure_ssh_keys(SSH_PRIVATE_KEY_PATH, SSH_PUBLIC_KEY_PATH)

    # Subnet details for bastion
    from .clients import build_clients  # type: ignore  # keep local import pattern consistent
    subnet = clients.vcn_client.get_subnet(target.subnet_id).data
    subnet_name = getattr(subnet, "display_name", "")
    subnet_compartment_id = subnet.compartment_id

    # Find or create bastion
    manager = BastionManager(clients)
    try:
        bastion_id = manager.find_or_create_bastion(
            subnet_id=target.subnet_id,
            subnet_name=subnet_name,
            subnet_compartment_id=subnet_compartment_id,
            vcn_id=target.vcn_id,
            allow_list=DEFAULT_ALLOW_LIST,
            dns_proxy_status=DEFAULT_FQDN_SOCKS,
        )
    except oci.exceptions.ServiceError as e:
        # Handle quota errors gracefully
        if getattr(e, "status", None) == 400 and ("QuotaExceeded" in str(e) or getattr(e, "code", "") == "QuotaExceeded"):
            error_exit("Bastion quota exceeded in this region for the tenancy. Please delete an existing Bastion or reuse an existing one in the target subnet/VCN.")
        raise

    # Resume or create
    sessions_all = manager.list_active_sessions(bastion_id)
    sessions = [s for s in sessions_all if _session_matches_target(s, target)]
    if args.debug:
        log("DEBUG", f"Active sessions on bastion: total={len(sessions_all)}, matching_target={len(sessions)}")
    action = "resume" if (args.resume and sessions) else ("new" if args.auto else choose_resume_or_new(bool(sessions)))

    if action == "resume" and sessions:
        # If multiple, prefer --resume-index
        chosen = sessions[0]
        if len(sessions) > 1:
            if args.resume_index and 1 <= args.resume_index <= len(sessions):
                chosen = sessions[args.resume_index - 1]
            else:
                def _label_session(i: int, s: SessionDescriptor) -> str:
                    ip_info = f" {s.target_private_ip}" if s.target_private_ip else ""
                    port_info = f":{s.target_port}" if s.target_port else ""
                    return f"{i+1}. {s.mode.value}{ip_info}{port_info} {s.session_id[:20]}... ({s.region})"
                labels = [_label_session(i, s) for i, s in enumerate(sessions)]
                print("\nActive sessions:")
                for line in labels:
                    print("  ", line)
                sel = input("Select session number to resume: ").strip()
                if sel.isdigit():
                    idx = int(sel) - 1
                    if 0 <= idx < len(sessions):
                        chosen = sessions[idx]
        # Refresh details to ensure bastion_user_name is populated
        session = manager.get_session_details(chosen.session_id)
        mode = session.mode
        if session.target_port:
            remote_port = session.target_port
        if mode == SessionMode.SOCKS:
            instance_ip = None  # not used for SOCKS
        else:
            instance_ip = session.target_private_ip or target.private_ip
        if mode == SessionMode.SSH and session.target_os_user:
            user = session.target_os_user
        log("INFO", f"Resuming Bastion session {session.session_id} ({mode.value})")
    else:
        # Create new session
        try:
            session = manager.create_session(
                mode=mode,
                bastion_id=bastion_id,
                ssh_public_key_path=SSH_PUBLIC_KEY_PATH,
                session_ttl=DEFAULT_SESSION_TTL,
                target_port=remote_port,
                instance_ip=target.private_ip,
                instance_ocid=target.ocid if target.resource_type == ResourceType.COMPUTE else None,
                os_user=user,
            )
        except oci.exceptions.ServiceError as e:
            msg = str(e)
            if getattr(e, "status", None) == 400 and "Managed SSH" in msg and "Bastion plugin" in msg and mode == SessionMode.SSH:
                log("ERROR", "Managed SSH failed because the Bastion plugin is disabled on the target instance.")
                if args.auto:
                    log("WARNING", "Falling back to Port-forwarding mode.")
                    mode = SessionMode.PFWD
                    # defaults when auto
                    local_port = args.local_port or 4444
                    remote_port = args.remote_port or 22
                else:
                    ans = input("Switch to Port-forwarding instead? [Y/n]: ").strip().lower()
                    if ans in ("", "y", "yes"):
                        mode = SessionMode.PFWD
                        local_port, remote_port = choose_ports(mode)
                        if local_port is None and remote_port is None:
                            error_exit("Operation cancelled by user")
                        if remote_port is None:
                            remote_port = DEFAULT_TARGET_PORT
                    else:
                        error_exit("Enable the Bastion plugin on the instance and retry.")
                # retry session creation in PFWD mode
                session = manager.create_session(
                    mode=mode,
                    bastion_id=bastion_id,
                    ssh_public_key_path=SSH_PUBLIC_KEY_PATH,
                    session_ttl=DEFAULT_SESSION_TTL,
                    target_port=remote_port,
                    instance_ip=target.private_ip,
                    instance_ocid=None,
                    os_user=user,
                )
            else:
                raise
        mode = session.mode
        if session.target_private_ip:
            instance_ip = session.target_private_ip
        if session.target_port:
            remote_port = session.target_port
        if mode == SessionMode.SOCKS:
            instance_ip = None
        if mode == SessionMode.SSH and session.target_os_user:
            user = session.target_os_user

    if args.debug:
        log("DEBUG", f"Target selected: name={target.name}, ip={target.private_ip}, type={target.resource_type.value}, compartment={target.compartment_name}")

    # Small grace period after session ACTIVE to avoid early publickey failures
    from .config import POST_SESSION_CONNECT_DELAY
    if POST_SESSION_CONNECT_DELAY and POST_SESSION_CONNECT_DELAY > 0:
        import time
        if args.debug:
            log("DEBUG", f"Sleeping {POST_SESSION_CONNECT_DELAY}s before attempting SSH connect...")
        time.sleep(POST_SESSION_CONNECT_DELAY)

    # Build and execute SSH command
    http_proxy = get_http_proxy_from_env()
    ssh_cmd = build_ssh_command(
        mode=mode.value,
        ssh_private_key=SSH_PRIVATE_KEY_PATH,
        http_proxy=http_proxy,
        local_port=local_port,
        instance_ip=instance_ip,
        target_port=remote_port,
        bastion_user_name=session.bastion_user_name,
        session_id=session.session_id,
        region=session.region,
    )
    rc = execute_ssh(ssh_cmd)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
