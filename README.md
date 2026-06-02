# qb (Interactive OCI Bastion Helper)

A brand-new interactive Python CLI to quickly connect to OCI Compute instances and DB nodes via the OCI Bastion service.

- Discovers resources in the current region (optionally filtered by compartment)
- Simple numbered prompts with fuzzy filtering
- Optional Textual TUI for interactive selection and tunnel/session display
- Default mode: Port-forwarding (fastest)
- Optional modes: Managed SSH (Compute only) and SOCKS (dynamic)
- Can resume existing Bastion sessions
- Automatically executes the SSH command

## Requirements
- Python 3.9+
- OCI Python SDK: `oci` (installed automatically if you `pip install .`)
- ssh-keygen available on PATH (for generating keys when missing)
- IAM permissions matching Bastion, Compute, VCN, Database read/list, and resource search

## Install (editable)

```
python -m pip install -e .
```

Install the optional TUI dependencies:

```
python -m pip install -e '.[tui]'
```

Or run without installing using the source tree:

```
PYTHONPATH=src python -m qb2.cli --help
```

## Usage Examples

Interactive flow (current region; prompt for compartment and target):

```
qb
```

Opt into the terminal UI:

```
qb --tui
```

Specify profile:

```
qb --profile MYPROFILE
```

Limit to a compartment:

```
qb --compartment ocid1.compartment.oc1... --profile MYPROFILE
```

Force a specific mode:

```
# Port-forwarding (default)
qb --mode PFWD

# Managed SSH (Compute only; requires Bastion agent RUNNING)
qb --mode SSH

# SOCKS dynamic proxy
qb --mode SOCKS --local-port 3128

# Auto-select an available local port
qb --mode PFWD --local-port auto

# Sync bastion allow-list with current public IP (/32)
qb --allowlist current-ip --allowlist-mode merge

# Show locally tracked recent sessions
qb --recent
```

Resume an existing session if available:

```
qb --resume
```

Override OS user and target/ports:

```
qb -u opc --remote-port 22 --local-port 4444
qb --mode PFWD -i 10.0.1.25 -r 1521 -l 11521
```

Providing `-i` with `-l` creates a direct port-forwarding session by target IP without prompting for a compartment or target resource.

## Notes
- The tool operates only in the current region of the selected profile.
- Compartment selection lets you narrow the discovery scope within the region.
- For interactive Compute port-forwarding, instances with multiple VNICs or private IPs show a target IP selector after the instance is selected.
- Managed SSH requires the Bastion plugin (instance agent) in RUNNING state on the Compute instance.
- When keys are missing, you will be prompted to generate an RSA keypair.
- HTTP(S) proxy from env (http_proxy/https_proxy) is included in the SSH ProxyCommand when needed.

## Uninstall

```
pip uninstall ociquickbastion-qb2
```
