from __future__ import annotations

import ipaddress
from urllib.request import urlopen


_PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://checkip.amazonaws.com",
)


def resolve_current_public_ipv4(timeout_sec: float = 3.0) -> str:
    errors = []
    for url in _PUBLIC_IP_ENDPOINTS:
        try:
            with urlopen(url, timeout=timeout_sec) as resp:  # nosec B310
                data = resp.read().decode("utf-8").strip()
            ip = str(ipaddress.IPv4Address(data))
            return ip
        except Exception as exc:  # pragma: no cover - exercised via fallback behavior
            errors.append(f"{url}: {exc}")
    raise RuntimeError(
        "Unable to resolve current public IPv4 from known providers. "
        f"Last errors: {'; '.join(errors)}"
    )
