from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .util import error_exit


def _confirm_generate_ssh_key() -> bool:
    try:
        reply = input("Do you want to generate a new RSA keypair? (Y/N) ").strip().lower()
    except EOFError:
        reply = "n"
    return reply.startswith("y")


def ensure_ssh_keys(
    private_key: str,
    public_key: str,
    confirm_generate: Optional[Callable[[], bool]] = None,
) -> None:
    pk = Path(private_key)
    pub = Path(public_key)
    if pk.exists() and pub.exists():
        return
    print(f"[WARNING] {private_key} not found")
    should_generate = confirm_generate() if confirm_generate is not None else _confirm_generate_ssh_key()
    if not should_generate:
        error_exit("SSH keys are required but not found")
    pk.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["ssh-keygen", "-t", "rsa", "-f", str(pk), "-N", ""], check=True)
    except FileNotFoundError:
        error_exit("ssh-keygen is not installed or not in PATH")
    except subprocess.CalledProcessError:
        error_exit("Failed to generate SSH keys")
    print("[SUCCESS] RSA keypair generated successfully")


def get_http_proxy_from_env() -> Optional[str]:
    return os.environ.get("http_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")


def _proxy_nc_opt(http_proxy: Optional[str]) -> str:
    return f"-o 'ProxyCommand=nc -X connect -x {http_proxy} %h %p'" if http_proxy else ""


def find_available_local_port(preferred: int, scan_start: int, scan_end: int) -> int:
    if scan_start > scan_end:
        raise ValueError("scan_start must be <= scan_end")

    def _is_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    if _is_free(preferred):
        return preferred

    for port in range(scan_start, scan_end + 1):
        if port == preferred:
            continue
        if _is_free(port):
            return port
    raise RuntimeError(f"No available local port found in range {scan_start}-{scan_end}")


def resolve_local_port(mode: str, requested: Optional[str], preferred_default: int) -> int:
    if mode not in ("PFWD", "SOCKS"):
        raise ValueError("resolve_local_port is only valid for PFWD/SOCKS modes")

    scan_start = 1024
    scan_end = 65535

    if requested is None or requested == "":
        return find_available_local_port(preferred_default, scan_start, scan_end)

    normalized = requested.strip().lower()
    if normalized == "auto":
        return find_available_local_port(preferred_default, scan_start, scan_end)

    if not normalized.isdigit():
        raise ValueError("local-port must be an integer 1..65535 or 'auto'")

    port = int(normalized)
    if not 1 <= port <= 65535:
        raise ValueError("local-port must be between 1 and 65535")
    return port


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
    base = f"ssh -i {ssh_private_key}"

    if mode == "PFWD":
        if not (local_port and instance_ip):
            error_exit("local-port and instance-ip are required for port-forwarding mode")
        cmd = f"{base} -N -L {local_port}:{instance_ip}:{target_port}"
        if http_proxy:
            cmd += f" {_proxy_nc_opt(http_proxy)}"
        cmd += f" -p 22 {bastion_user_name}@host.bastion.{region}.oci.oraclecloud.com"
        return cmd

    if mode == "SOCKS":
        if not local_port:
            error_exit("local-port is required for SOCKS mode")
        cmd = f"{base} -N -D 127.0.0.1:{local_port}"
        if http_proxy:
            cmd += f" {_proxy_nc_opt(http_proxy)}"
        cmd += f" -p 22 {bastion_user_name}@host.bastion.{region}.oci.oraclecloud.com"
        return cmd

    # Managed SSH
    if not instance_ip:
        error_exit("instance-ip is required for managed SSH mode")
    inner = f"ssh -i {ssh_private_key} "
    if http_proxy:
        inner += f"{_proxy_nc_opt(http_proxy)} "
    inner += f"-W %h:%p -p 22 {session_id}@host.bastion.{region}.oci.oraclecloud.com"
    cmd = f'{base} -o ProxyCommand="{inner}" -p 22 {bastion_user_name}@{instance_ip}'
    return cmd


def execute_ssh(cmd: str) -> int:
    print("\nType the following SSH command to connect:\n")
    print(cmd)
    print()
    try:
        return subprocess.call(cmd, shell=True)
    except KeyboardInterrupt:
        return 130
