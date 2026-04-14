from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .util import error_exit


def ensure_ssh_keys(private_key: str, public_key: str) -> None:
    pk = Path(private_key)
    pub = Path(public_key)
    if pk.exists() and pub.exists():
        return
    print(f"[WARNING] {private_key} not found")
    try:
        reply = input("Do you want to generate a new RSA keypair? (Y/N) ").strip().lower()
    except EOFError:
        reply = "n"
    if not reply.startswith("y"):
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
