from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from typing import List, Optional

from .config import (
    DEFAULT_ALLOW_LIST,
    DEFAULT_FQDN_SOCKS,
    DEFAULT_OS_USERNAME,
    DEFAULT_PFWD_LOCAL_PORT,
    DEFAULT_SESSION_TTL,
    DEFAULT_SOCKS_LOCAL_PORT,
    DEFAULT_TARGET_PORT,
    SESSION_REGISTRY_PATH,
    SSH_PRIVATE_KEY_PATH,
    SSH_PUBLIC_KEY_PATH,
)
from .types import (
    AllowListMode,
    LocalPortRequest,
    LocalSessionRecord,
    SessionMode,
    ResourceType,
    TargetPrivateIp,
    TargetResource,
    SessionDescriptor,
)
from .util import log, error_exit, set_log_sink


def parse_args():
    p = argparse.ArgumentParser(description="Interactive OCI Bastion helper (Compute & DB nodes)")
    p.add_argument("--profile", "-p", default="DEFAULT", help="OCI config profile name")
    p.add_argument("--compartment", help="Compartment OCID to limit discovery (default: all in current region)")
    p.add_argument("--region", help="Override region from profile (e.g., eu-paris-1)")
    p.add_argument("--mode", choices=["PFWD", "SSH", "SOCKS"], help="Session mode (default PFWD)")
    p.add_argument("--user", "-u", default=DEFAULT_OS_USERNAME, help="Remote OS username")
    p.add_argument("--instance-ip", "-i", help="Target private IP for port-forwarding")
    p.add_argument("--remote-port", "-r", type=int, help="Remote/target port (PFWD/SSH; default 22)")
    p.add_argument("--local-port", "-l", type=str, help="Local port (PFWD/SOCKS) or 'auto'")
    p.add_argument("--allowlist", choices=["default", "current-ip"], default="default", help="Allow-list policy for bastion client CIDRs")
    p.add_argument("--allowlist-mode", choices=["merge", "strict", "prompt"], default="merge", help="Mode for --allowlist current-ip")
    p.add_argument("--recent", action="store_true", help="Show recent local sessions and exit")
    p.add_argument("--resume", action="store_true", help="Try to resume an existing session if available")
    p.add_argument("--resume-index", type=int, help="Index (1-based) of session to resume when multiple exist")
    p.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    p.add_argument("--timeout", type=float, default=20.0, help="SDK connect/read timeout in seconds (default 20)")
    p.add_argument("--sdk-proxy", help="HTTP(S) proxy for OCI SDK requests, e.g. http://host:port")
    p.add_argument("--include-states-compute", help="Comma-separated lifecycle states to include for Compute (e.g., RUNNING,STOPPED,PROVISIONING)")
    p.add_argument("--auto", action="store_true", help="Auto-select first resource and default options (non-interactive)")
    p.add_argument("--tui", action="store_true", help="Use the optional Textual terminal UI for interactive selections")
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


def _preferred_local_port(mode: SessionMode) -> int:
    if mode == SessionMode.SOCKS:
        return DEFAULT_SOCKS_LOCAL_PORT
    return DEFAULT_PFWD_LOCAL_PORT


def _resolve_local_port_request(mode: SessionMode, requested_raw: Optional[str], resolve_local_port_fn) -> LocalPortRequest:
    if mode not in (SessionMode.PFWD, SessionMode.SOCKS):
        return LocalPortRequest(mode=mode, requested=None, auto=False, resolved=None)
    requested = (requested_raw or "").strip()
    auto = requested.lower() == "auto" or requested == ""
    requested_int = int(requested) if requested.isdigit() else None
    try:
        resolved = resolve_local_port_fn(mode.value, requested_raw, _preferred_local_port(mode))
    except ValueError as exc:
        error_exit(str(exc))
    except RuntimeError as exc:
        error_exit(str(exc))
    return LocalPortRequest(mode=mode, requested=requested_int, auto=auto, resolved=resolved)


def _effective_instance_ip(mode: SessionMode, requested_ip: Optional[str], target_ip: str) -> Optional[str]:
    if mode == SessionMode.PFWD:
        return (requested_ip or "").strip() or target_ip
    if mode == SessionMode.SOCKS:
        return None
    return target_ip


def _is_direct_pfwd_request(args) -> bool:
    requested_mode = SessionMode(args.mode) if args.mode else SessionMode.PFWD
    requested_ip = (args.instance_ip or "").strip()
    requested_local_port = "" if args.local_port is None else str(args.local_port).strip()
    return (
        requested_mode == SessionMode.PFWD
        and bool(requested_ip)
        and bool(requested_local_port)
    )


def _should_offer_private_ip_selector(args, mode: SessionMode, target: TargetResource, direct_pfwd_request: bool) -> bool:
    return (
        mode == SessionMode.PFWD
        and target.resource_type == ResourceType.COMPUTE
        and not args.auto
        and not direct_pfwd_request
        and not bool((args.instance_ip or "").strip())
    )


def _apply_target_private_ip(target: TargetResource, private_ip: TargetPrivateIp) -> TargetResource:
    target.private_ip = private_ip.ip_address
    target.subnet_id = private_ip.subnet_id or target.subnet_id
    target.vcn_id = private_ip.vcn_id or target.vcn_id
    return target


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main():
    args = parse_args()

    # Local imports to avoid importing OCI SDK unless executing
    from .session_registry import list_recent_sessions, upsert_session_record
    if args.recent:
        rows = list_recent_sessions(SESSION_REGISTRY_PATH, limit=20)
        if not rows:
            print("No local sessions found.")
        else:
            print("Recent local sessions:")
            for i, row in enumerate(rows, 1):
                lp = f" local:{row.local_port}" if row.local_port else ""
                rp = f" remote:{row.remote_port}" if row.remote_port else ""
                ip = f" {row.target_private_ip}" if row.target_private_ip else ""
                print(f"  {i}. {row.mode.value}{ip}{lp}{rp} {row.session_id[:20]}... {row.last_used_at_utc}")
        raise SystemExit(0)

    import oci

    from .clients import load_profile, build_clients, list_compartments
    from .bastion import BastionManager
    from .net import resolve_current_public_ipv4
    from .ssh import (
        ensure_ssh_keys,
        build_ssh_command,
        get_http_proxy_from_env,
        execute_ssh,
        resolve_local_port,
    )
    if args.tui:
        try:
            from . import tui as ui
            ui.ensure_available()
            set_log_sink(ui.capture_log)
        except Exception as exc:
            error_exit(str(exc))
    else:
        from . import prompts as ui

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

    direct_pfwd_request = _is_direct_pfwd_request(args)
    user = args.user or DEFAULT_OS_USERNAME
    local_port_request = LocalPortRequest(mode=SessionMode.PFWD, requested=None, auto=False, resolved=None)
    subnet = None

    if direct_pfwd_request:
        from .discovery import resolve_subnet_from_private_ip

        mode = SessionMode.PFWD
        instance_ip = (args.instance_ip or "").strip()
        local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
        local_port = local_port_request.resolved
        remote_port = args.remote_port or DEFAULT_TARGET_PORT
        try:
            if args.tui:
                subnet_id = ui.run_with_status(
                    "Resolving subnet from target IP",
                    lambda: resolve_subnet_from_private_ip(clients, instance_ip),
                )
                subnet = ui.run_with_status(
                    "Loading subnet details",
                    lambda: clients.vcn_client.get_subnet(subnet_id).data,
                )
            else:
                subnet_id = resolve_subnet_from_private_ip(clients, instance_ip)
                subnet = clients.vcn_client.get_subnet(subnet_id).data
        except RuntimeError as exc:
            error_exit(f"Could not resolve subnet for target IP {instance_ip}: {exc}")
        target = TargetResource(
            ocid="",
            name=instance_ip,
            resource_type=ResourceType.COMPUTE,
            private_ip=instance_ip,
            compartment_id=getattr(subnet, "compartment_id", ""),
            compartment_name="",
            vcn_id=getattr(subnet, "vcn_id", ""),
            subnet_id=subnet_id,
            region=cfg.get("region", ""),
            state="DIRECT",
        )
        log("INFO", f"Using direct port-forward target {instance_ip}:{remote_port}")
    else:
        if args.tui:
            compartments = ui.run_with_status(
                "Loading compartments",
                lambda: list_compartments(clients.identity_client, tenancy_id),
            )
        else:
            compartments = list_compartments(clients.identity_client, tenancy_id)

        # Choose compartment (optional)
        selected_compartment = args.compartment
        if not selected_compartment:
            selected_compartment = ui.choose_compartment(compartments)
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
        if args.tui:
            resources = ui.run_with_status(
                "Discovering resources in current region",
                lambda: _gather_resources(
                    compartments,
                    selected_compartment,
                    clients,
                    include_states_compute=include_states_compute,
                    debug=args.debug,
                ),
            )
        else:
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
            target = ui.choose_target(resources)
        if not target:
            error_exit("Operation cancelled by user")
        if len(resources) == 1:
            log("INFO", f"Selected only available target: {target.name} ({target.private_ip})")
        instance_ip = target.private_ip

        # Choose mode and ports/user
        if args.auto:
            mode = SessionMode(args.mode) if args.mode else SessionMode.PFWD
            if mode == SessionMode.PFWD:
                local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                local_port = local_port_request.resolved
                remote_port = args.remote_port or 22
            elif mode == SessionMode.SOCKS:
                local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                local_port = local_port_request.resolved
                remote_port = 22
            else:
                local_port = None
                remote_port = args.remote_port or 22
        else:
            if args.resume:
                mode = SessionMode(args.mode) if args.mode else SessionMode.PFWD
                if mode == SessionMode.PFWD:
                    local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                    local_port = local_port_request.resolved
                    remote_port = args.remote_port or DEFAULT_TARGET_PORT
                elif mode == SessionMode.SOCKS:
                    local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                    local_port = local_port_request.resolved
                    remote_port = 22
                else:
                    local_port = None
                    remote_port = args.remote_port or 22
            else:
                mode = SessionMode(args.mode) if args.mode else ui.choose_mode(SessionMode.PFWD)
                # Managed SSH allowed only for Compute instances
                if mode == SessionMode.SSH and target.resource_type != ResourceType.COMPUTE:
                    log("WARNING", "Managed SSH not supported for DB nodes; switching to Port-forwarding")
                    mode = SessionMode.PFWD
                if mode in (SessionMode.PFWD, SessionMode.SOCKS):
                    proposed_local = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                    local_port_request = proposed_local
                    local_port, remote_port = ui.choose_ports(mode, default_local_port=proposed_local.resolved)
                else:
                    local_port, remote_port = ui.choose_ports(mode, default_local_port=None)
                # If user cancelled during port prompts
                if local_port is None and remote_port is None:
                    error_exit("Operation cancelled by user")
                if remote_port is None:
                    remote_port = DEFAULT_TARGET_PORT

    if _should_offer_private_ip_selector(args, mode, target, direct_pfwd_request):
        from .discovery import list_compute_private_ips

        try:
            if args.tui:
                private_ip_choices = ui.run_with_status(
                    "Loading compute private IPs",
                    lambda: list_compute_private_ips(clients, target.ocid, target.compartment_id),
                )
            else:
                private_ip_choices = list_compute_private_ips(clients, target.ocid, target.compartment_id)
        except Exception as exc:
            private_ip_choices = []
            if args.debug:
                log("DEBUG", f"Could not load private IP choices for {target.name}: {exc}")
        if len(private_ip_choices) > 1:
            selected_private_ip = ui.choose_private_ip(private_ip_choices)
            if selected_private_ip is None:
                error_exit("Operation cancelled by user")
            _apply_target_private_ip(target, selected_private_ip)
            instance_ip = target.private_ip
            log("INFO", f"Selected target private IP {target.private_ip}")

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
                local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                local_port = local_port_request.resolved
                remote_port = args.remote_port or 22
            else:
                if ui.confirm_switch_to_pfwd():
                    mode = SessionMode.PFWD
                    proposed_local = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                    local_port_request = proposed_local
                    local_port, remote_port = ui.choose_ports(mode, default_local_port=proposed_local.resolved)
                    if local_port is None and remote_port is None:
                        error_exit("Operation cancelled by user")
                    if remote_port is None:
                        remote_port = DEFAULT_TARGET_PORT
                else:
                    error_exit("Managed SSH cannot proceed until the Bastion plugin is enabled on the instance.")

    # Ensure SSH keys
    ensure_ssh_keys(
        SSH_PRIVATE_KEY_PATH,
        SSH_PUBLIC_KEY_PATH,
        confirm_generate=getattr(ui, "confirm_generate_ssh_key", None),
    )

    # Subnet details for bastion
    from .clients import build_clients  # type: ignore  # keep local import pattern consistent
    if subnet is None:
        if args.tui:
            subnet = ui.run_with_status(
                "Loading subnet details",
                lambda: clients.vcn_client.get_subnet(target.subnet_id).data,
            )
        else:
            subnet = clients.vcn_client.get_subnet(target.subnet_id).data
    subnet_name = getattr(subnet, "display_name", "")
    subnet_compartment_id = subnet.compartment_id

    # Find or create bastion
    manager = BastionManager(clients)
    try:
        if args.tui:
            bastion_id = ui.run_with_status(
                "Finding or creating Bastion",
                lambda: manager.find_or_create_bastion(
                    subnet_id=target.subnet_id,
                    subnet_name=subnet_name,
                    subnet_compartment_id=subnet_compartment_id,
                    vcn_id=target.vcn_id,
                    allow_list=DEFAULT_ALLOW_LIST,
                    dns_proxy_status=DEFAULT_FQDN_SOCKS,
                ),
            )
        else:
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

    if args.allowlist == "current-ip":
        try:
            if args.tui:
                public_ip = ui.run_with_status(
                    "Resolving current public IP",
                    lambda: resolve_current_public_ipv4(timeout_sec=3.0),
                )
            else:
                public_ip = resolve_current_public_ipv4(timeout_sec=3.0)
        except RuntimeError as exc:
            error_exit(str(exc))
        current_ip_cidr = f"{public_ip}/32"
        mode_for_update = AllowListMode(args.allowlist_mode)
        if mode_for_update == AllowListMode.PROMPT:
            if args.auto:
                mode_for_update = AllowListMode.MERGE
            else:
                mode_for_update = ui.choose_allowlist_mode()
        desired_cidrs = manager.reconcile_allow_list(
            bastion_id=bastion_id,
            current_ip_cidr=current_ip_cidr,
            mode=mode_for_update,
        )
        current_cidrs = list(getattr(manager.get_bastion_details(bastion_id), "client_cidr_block_allow_list", []) or [])
        if desired_cidrs != current_cidrs:
            if args.tui:
                ui.run_with_status(
                    "Updating Bastion allow-list",
                    lambda: manager.update_allow_list(bastion_id, desired_cidrs),
                )
            else:
                manager.update_allow_list(bastion_id, desired_cidrs)
            log("INFO", f"Updated bastion allow-list with {current_ip_cidr} ({mode_for_update.value})")
        else:
            log("INFO", f"Bastion allow-list already includes {current_ip_cidr}; no update needed")

    # Resume or create
    if args.tui:
        sessions_all = ui.run_with_status(
            "Loading active Bastion sessions",
            lambda: manager.list_active_sessions(bastion_id),
        )
    else:
        sessions_all = manager.list_active_sessions(bastion_id)
    sessions = [s for s in sessions_all if _session_matches_target(s, target)]
    if args.debug:
        log("DEBUG", f"Active sessions on bastion: total={len(sessions_all)}, matching_target={len(sessions)}")
    action = "resume" if (args.resume and sessions) else ("new" if (args.auto or direct_pfwd_request) else ui.choose_resume_or_new(bool(sessions)))
    session_status_hint = "created"

    if action == "resume" and sessions:
        # If multiple, prefer --resume-index
        chosen = sessions[0]
        if len(sessions) > 1:
            if args.resume_index and 1 <= args.resume_index <= len(sessions):
                chosen = sessions[args.resume_index - 1]
            else:
                chosen = ui.choose_session(sessions) or chosen
        # Refresh details to ensure bastion_user_name is populated
        if args.tui:
            session = ui.run_with_status(
                "Loading session details",
                lambda: manager.get_session_details(chosen.session_id),
            )
        else:
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
        session_status_hint = "resumed"
    else:
        # Create new session
        try:
            create_session = lambda: manager.create_session(
                mode=mode,
                bastion_id=bastion_id,
                ssh_public_key_path=SSH_PUBLIC_KEY_PATH,
                session_ttl=DEFAULT_SESSION_TTL,
                target_port=remote_port,
                instance_ip=_effective_instance_ip(mode, args.instance_ip, target.private_ip),
                instance_ocid=target.ocid if target.ocid and target.resource_type == ResourceType.COMPUTE else None,
                os_user=user,
            )
            session = ui.run_with_status("Creating Bastion session", create_session) if args.tui else create_session()
        except oci.exceptions.ServiceError as e:
            msg = str(e)
            if getattr(e, "status", None) == 400 and "Managed SSH" in msg and "Bastion plugin" in msg and mode == SessionMode.SSH:
                log("ERROR", "Managed SSH failed because the Bastion plugin is disabled on the target instance.")
                if args.auto:
                    log("WARNING", "Falling back to Port-forwarding mode.")
                    mode = SessionMode.PFWD
                    local_port_request = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                    local_port = local_port_request.resolved
                    remote_port = args.remote_port or 22
                else:
                    if ui.confirm_switch_to_pfwd():
                        mode = SessionMode.PFWD
                        proposed_local = _resolve_local_port_request(mode, args.local_port, resolve_local_port)
                        local_port_request = proposed_local
                        local_port, remote_port = ui.choose_ports(mode, default_local_port=proposed_local.resolved)
                        if local_port is None and remote_port is None:
                            error_exit("Operation cancelled by user")
                        if remote_port is None:
                            remote_port = DEFAULT_TARGET_PORT
                    else:
                        error_exit("Enable the Bastion plugin on the instance and retry.")
                # retry session creation in PFWD mode
                retry_create_session = lambda: manager.create_session(
                    mode=mode,
                    bastion_id=bastion_id,
                    ssh_public_key_path=SSH_PUBLIC_KEY_PATH,
                    session_ttl=DEFAULT_SESSION_TTL,
                    target_port=remote_port,
                    instance_ip=_effective_instance_ip(mode, args.instance_ip, target.private_ip),
                    instance_ocid=None,
                    os_user=user,
                )
                session = ui.run_with_status("Creating Port-forwarding session", retry_create_session) if args.tui else retry_create_session()
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
        session_status_hint = "created"

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
    timestamp = _now_utc_iso()
    record = LocalSessionRecord(
        session_id=session.session_id,
        target_ocid=target.ocid or None,
        target_name=target.name,
        target_private_ip=instance_ip or target.private_ip,
        mode=mode,
        region=session.region,
        local_port=local_port,
        remote_port=remote_port,
        os_user=user,
        created_at_utc=timestamp,
        last_used_at_utc=timestamp,
        status_hint=session_status_hint,
    )
    upsert_session_record(SESSION_REGISTRY_PATH, record)

    if args.tui:
        rc = ui.run_ssh_command(ssh_cmd, mode)
    else:
        rc = execute_ssh(ssh_cmd)
    post_record = LocalSessionRecord(
        session_id=session.session_id,
        target_ocid=target.ocid or None,
        target_name=target.name,
        target_private_ip=instance_ip or target.private_ip,
        mode=mode,
        region=session.region,
        local_port=local_port,
        remote_port=remote_port,
        os_user=user,
        created_at_utc=timestamp,
        last_used_at_utc=_now_utc_iso(),
        status_hint="ssh_started" if rc == 0 else "ssh_exit_nonzero",
    )
    upsert_session_record(SESSION_REGISTRY_PATH, post_record)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
