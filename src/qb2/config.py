from pathlib import Path

DEFAULT_SESSION_TTL = 10800
DEFAULT_TARGET_PORT = 22
DEFAULT_OS_USERNAME = "opc"
DEFAULT_ALLOW_LIST = ["0.0.0.0/0"]
DEFAULT_FQDN_SOCKS = "ENABLED"
# Empirically, OCI Bastion may need a few seconds after session ACTIVE before SSH accepts the key
POST_SESSION_CONNECT_DELAY = 6

SSH_PRIVATE_KEY_PATH = str(Path.home() / ".ssh" / "id_rsa")
SSH_PUBLIC_KEY_PATH = f"{SSH_PRIVATE_KEY_PATH}.pub"
