from __future__ import annotations

from typing import Iterable, List, Optional

from .labels import compartment_path_labels, sort_compartments_by_hierarchy, target_private_ip_label
from .types import TargetPrivateIp, TargetResource, SessionDescriptor, SessionMode, ResourceType, AllowListMode
from .util import fuzzy_filter, log


def _prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


def _choose_from_list(options: List[str], title: str, q_hint: str = "quit") -> Optional[str]:
    if not options:
        return None
    original = list(options)
    current = list(options)
    while True:
        print(f"\n{title}")
        for i, opt in enumerate(current, 1):
            print(f"  {i}. {opt}")
        ans = _prompt(f"Select a number (or 'f <query>' to filter, 'q' to {q_hint}): ").strip()
        if not ans:
            # Default to first option for convenience
            if current:
                return current[0]
        low = ans.lower()
        if low == "q":
            return None
        # Inline filter: support both "f <query>" and bare query text
        from .util import fuzzy_filter
        if low.startswith("f "):
            query = ans[2:].strip()
            # Strict substring filtering for f <query>
            q = query.lower()
            matches = [x for x in original if q in x.lower()]
            if not matches:
                print("No matches. Showing all.")
                current = list(original)
            elif len(matches) == 1:
                return matches[0]
            else:
                current = matches
            continue
        if not ans.isdigit():
            # Treat plain text as a filter query (substring preferred via fuzzy_filter)
            current = fuzzy_filter(original, ans)
            if not current:
                print("No matches. Showing all.")
                current = list(original)
            elif len(current) == 1:
                return current[0]
            continue
        # Numeric selection
        idx = int(ans) if ans.isdigit() else -1
        if 1 <= idx <= len(current):
            return current[idx - 1]
        print("Invalid selection. Try again.")


def choose_compartment(compartments: List[dict]) -> Optional[str]:
    # Present an explicit "All compartments" option and keep 'q' as quit
    ordered_compartments = sort_compartments_by_hierarchy(compartments)
    labels = compartment_path_labels(ordered_compartments)
    opts = ["All compartments (current region)"] + [labels[c["id"]] for c in ordered_compartments]
    selected = _choose_from_list(opts, title="Choose a compartment", q_hint="quit")
    if selected is None:
        # User chose to quit
        return ""  # special sentinel meaning QUIT
    if selected.startswith("All compartments"):
        return None  # None means use all
    for c in ordered_compartments:
        if labels[c["id"]] == selected:
            return c["id"]
    return None


def choose_resource_type() -> List[ResourceType]:
    # For now, fixed to both types; we could add a menu if needed
    return [ResourceType.COMPUTE, ResourceType.DBNODE]


def choose_target(resources: List[TargetResource]) -> Optional[TargetResource]:
    if not resources:
        print("No resources available.")
        return None
    if len(resources) == 1:
        return resources[0]
    def label(r: TargetResource) -> str:
        return f"{r.name} | {r.private_ip} | {r.resource_type.value} | {r.compartment_name}"
    opts = [label(r) for r in resources]
    selected = _choose_from_list(opts, title="Choose a target")
    if selected is None:
        return None
    for r in resources:
        if label(r) == selected:
            return r
    return None


def choose_private_ip(private_ips: List[TargetPrivateIp]) -> Optional[TargetPrivateIp]:
    if not private_ips:
        return None
    if len(private_ips) == 1:
        return private_ips[0]
    opts = [target_private_ip_label(private_ip) for private_ip in private_ips]
    selected = _choose_from_list(opts, title="Choose target private IP")
    if selected is None:
        return None
    for private_ip in private_ips:
        if target_private_ip_label(private_ip) == selected:
            return private_ip
    return None


def choose_mode(default: SessionMode = SessionMode.PFWD) -> SessionMode:
    opts = [
        f"Port-forwarding (default)",
        "Managed SSH",
        "SOCKS (dynamic port forwarding)",
    ]
    selected = _choose_from_list(opts, title="Choose session mode")
    if selected is None:
        return default
    try:
        idx = opts.index(selected)
    except ValueError:
        return default
    return [SessionMode.PFWD, SessionMode.SSH, SessionMode.SOCKS][idx]


def choose_ports(mode: SessionMode, default_local_port: Optional[int] = None) -> (Optional[int], Optional[int]):
    def read_port(prompt: str, default: int) -> Optional[int]:
        while True:
            val = _prompt(prompt).strip()
            if val.lower() == "q":
                return None
            if val == "":
                return default
            if val.isdigit():
                port = int(val)
                if 1 <= port <= 65535:
                    return port
            print("Invalid port. Enter a number between 1 and 65535 or 'q' to cancel.")

    if mode == SessionMode.PFWD:
        dflt = default_local_port if default_local_port is not None else 4444
        l = read_port(f"Local port (default auto-selected {dflt}) [q to cancel]: ", dflt)
        if l is None:
            return None, None
        r = read_port("Remote port (default 22) [q to cancel]: ", 22)
        if r is None:
            return None, None
        return l, r
    if mode == SessionMode.SOCKS:
        dflt = default_local_port if default_local_port is not None else 3128
        l = read_port(f"Local port (default auto-selected {dflt}) [q to cancel]: ", dflt)
        if l is None:
            return None, None
        return l, 22
    # Managed SSH
    r = read_port("Target port (default 22) [q to cancel]: ", 22)
    if r is None:
        return None, None
    return None, r


def choose_user(default_user: str = "opc") -> str:
    u = _prompt(f"OS user (default {default_user}): ").strip()
    return u or default_user


def confirm_switch_to_pfwd() -> bool:
    ans = _prompt("Switch to Port-forwarding instead? [Y/n]: ").strip().lower()
    return ans in ("", "y", "yes")


def choose_allowlist_mode() -> AllowListMode:
    ans = _prompt("Allow-list mode for current IP: merge or strict? [merge/strict]: ").strip().lower()
    return AllowListMode.STRICT if ans == "strict" else AllowListMode.MERGE


def choose_resume_or_new(has_sessions: bool) -> str:
    if not has_sessions:
        return "new"
    opts = ["Resume existing session", "Create new session"]
    selected = _choose_from_list(opts, title="Session action")
    if selected is None:
        return "new"
    return "resume" if selected == opts[0] else "new"


def choose_session(sessions: List[SessionDescriptor]) -> Optional[SessionDescriptor]:
    if not sessions:
        return None

    def label(i: int, s: SessionDescriptor) -> str:
        ip_info = f" {s.target_private_ip}" if s.target_private_ip else ""
        port_info = f":{s.target_port}" if s.target_port else ""
        return f"{i + 1}. {s.mode.value}{ip_info}{port_info} {s.session_id[:20]}... ({s.region})"

    selected = _choose_from_list([label(i, s) for i, s in enumerate(sessions)], title="Active sessions")
    if selected is None:
        return None
    for i, session in enumerate(sessions):
        if label(i, session) == selected:
            return session
    return None
