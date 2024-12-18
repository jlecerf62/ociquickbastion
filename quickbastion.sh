#!/bin/bash

# Script: quickbastion.sh
# Description: Create and manage OCI BASTION sessions with enhanced error handling and validation
# Dependencies: oci-cli, jq, ssh-keygen

set -o errexit  # Exit on error
set -o nounset  # Exit on undefined variable
set -o pipefail # Exit on pipe failure

# Constants
readonly DEFAULT_SESSION_TTL=10800
readonly DEFAULT_TARGET_PORT=22
readonly DEFAULT_OS_USERNAME="opc"
readonly DEFAULT_PROFILE="DEFAULT"
readonly DEFAULT_ALLOW_LIST='["0.0.0.0/0"]'
readonly DEFAULT_FQDN_SOCKS="ENABLED"
readonly DEFAULT_HTTP_PROXY=""

# Variables
ssh_private_key="${HOME}/.ssh/id_rsa"
ssh_public_key="${HOME}/.ssh/id_rsa.pub"
bastion_id=""
is_agent_enabled=""
session_ttl="${DEFAULT_SESSION_TTL}"
target_port="${DEFAULT_TARGET_PORT}"
local_port="<local_port>"
target_os_username="${DEFAULT_OS_USERNAME}"
connection_mode="ssh"
instance_ip=""
instance_ocid=""
subnet_id=""
profile="${DEFAULT_PROFILE}"
allow_list="${DEFAULT_ALLOW_LIST}"
fqdn_socks="${DEFAULT_FQDN_SOCKS}"
http_proxy="${DEFAULT_HTTP_PROXY:-$https_proxy}"

# Error handling function
error_exit() {
    local message="$1"
    echo "ERROR: ${message}" >&2
    exit 1
}

# Logging function
log() {
    local level="$1"
    shift
    echo "[${level}] $*"
}

Help() {
    cat << EOF
Create OCI BASTION session.

Usage: quickbastion.sh [-h|i|r|u|p|l|s] <instance ocid>

Options:
    -h     Print this Help
    -i     Instance IP
    -r     Remote tcp port (port-forwarding)
    -u     Remote username (default: ${DEFAULT_OS_USERNAME})
    -p     OCI-CLI config profile (default: ${DEFAULT_PROFILE})
    -l     Local tcp port (port-forwarding)
    -s     Subnet ocid (SOCKS5 proxy)

Example:
    quickbastion.sh -p TENANT1 -u user1 ocid1.instance.oc1...
    quickbastion.sh -p TENANT2 -l 4443 -r 443 -i 10.0.0.1
    quickbastion.sh -p TENANT3 -l 4444 -s ocid1.subnet.oc1...

EOF
    exit 1
}

check_dependencies() {
    local deps=("oci" "jq" "ssh-keygen")
    for dep in "${deps[@]}"; do
        if ! command -v "${dep}" >/dev/null 2>&1; then
            error_exit "Required dependency '${dep}' is not installed"
        fi
    done
}

generate_ssh_keys() {
    log "INFO" "Generating new RSA keypair..."
    if ! ssh-keygen -t rsa -f "${HOME}/.ssh/id_rsa" -N ""; then
        error_exit "Failed to generate SSH keys"
    fi
    log "SUCCESS" "RSA keypair generated successfully"
}

check_ssh_keys() {
    log "INFO" "Searching for SSH key..."
    if [ ! -f "${ssh_private_key}" ] || [ ! -f "${ssh_public_key}" ]; then
        log "WARNING" "${HOME}/.ssh/id_rsa not found"
        read -rp "Do you want to generate a new RSA keypair? (Y/N) " -n 1 reply
        echo
        if [[ ${reply} =~ ^[Yy]$ ]]; then
            generate_ssh_keys
        else
            error_exit "SSH keys are required but not found"
        fi
    else 
        log "INFO" "${HOME}/.ssh/id_rsa found"
    fi
}

check_bastion_plugin() {
    if [ "${connection_mode}" = "ssh" ]; then
        log "INFO" "Detecting Bastion plugin state..."
        local get_instance
        get_instance=$(oci compute instance get --instance-id "${instance_ocid}" --profile "${profile}") || error_exit "Failed to get instance details"
        
        local instance_name
        instance_name=$(echo -E "${get_instance}" | jq -r '.data."display-name"')
        
        local instance_compartment_id
        instance_compartment_id=$(echo -E "${get_instance}" | jq -r '.data."compartment-id"')
        
        is_agent_enabled=$(oci instance-agent plugin get \
            --instanceagent-id "${instance_ocid}" \
            -c "${instance_compartment_id}" \
            --plugin-name Bastion \
            --profile "${profile}" | jq -r '.data|select(.status=="RUNNING")') || error_exit "Failed to check plugin status"
        
        if [ -z "${is_agent_enabled}" ]; then
            error_exit "Bastion plugin is not enabled on ${instance_name}. Please enable bastion plugin and retry later."
        fi
        log "SUCCESS" "Bastion plugin is in RUNNING state on instance ${instance_name}"
    fi
}

create_bastion_service() {
    local subnet_name="$1"
    local subnet_compartment_id="$2"
    local subnet_id="$3"
    
    log "INFO" "Creating Bastion QuickBastion${subnet_name}..."
    log "INFO" "This may takes up to 2 minutes..."
    
    bastion_id=$(oci bastion bastion create \
        --bastion-type "standard" \
        -c "${subnet_compartment_id}" \
        --target-subnet-id "${subnet_id}" \
        --name "QuickBastion${subnet_name}" \
        --client-cidr-list "${allow_list}" \
        --dns-proxy-status "${fqdn_socks}" \
        --wait-for-state "SUCCEEDED" \
        --profile "${profile}" | jq -r '.data.id') || error_exit "Failed to create bastion service"
        
    log "SUCCESS" "Bastion service created successfully"
}

create_session() {
    log "INFO" "Creating session... This may takes up to 2 minutes..."
    
    ssh_public_key_content=$(cat "${ssh_public_key}")
    local session_id bastion_user_name region
    if [ "${connection_mode}" = "pfwd" ]; then
        session_id=$(oci bastion session create-port-forwarding \
            --bastion-id "${bastion_id}" \
            --ssh-public-key-file="${ssh_public_key}" \
            --target-port="${target_port}" \
            --target-private-ip="${instance_ip}" \
            --session-ttl="${session_ttl}" \
            --wait-for-state "SUCCEEDED" \
            --profile "${profile}" | jq -r '.data.resources[].identifier') || error_exit "Failed to create port forwarding session"
    elif [ "${connection_mode}" = "socks" ]; then
        session_id=$(oci bastion session create-session-create-dynamic-port-forwarding-session-target-resource-details \
            --bastion-id "${bastion_id}" \
            --key-details '{"publicKeyContent":"'"$ssh_public_key_content"'"}' \
            --session-ttl-in-seconds="${session_ttl}" \
            --wait-for-state "SUCCEEDED" \
            --profile "${profile}" | jq -r '.data.resources[].identifier') || error_exit "Failed to create SOCKS5 session"
    else
        session_id=$(oci bastion session create-managed-ssh \
            --bastion-id "${bastion_id}" \
            --ssh-public-key-file="${ssh_public_key}" \
            --target-os-username="${target_os_username}" \
            --target-port="${target_port}" \
            --target-resource-id="${instance_ocid}" \
            --target-private-ip="${instance_ip}" \
            --session-ttl="${session_ttl}" \
            --wait-for-state "SUCCEEDED" \
            --profile "${profile}" | jq -r '.data.resources[].identifier') || error_exit "Failed to create managed SSH session"
    fi
    bastion_user_name=$(oci bastion session get --session-id "$session_id" --profile "$profile" | jq -r '.data."bastion-user-name"')
    region=$(echo -E "${session_id}" | awk -F '.' '{print $4}')
    log "SUCCESS" "Session has been created. Session lifetime is ${session_ttl} seconds"
    echo
    echo "Type the following SSH command to connect to bastion session: "
    echo

# Define base SSH command
base_ssh_command="ssh -i $ssh_private_key"

# Define proxy command prefix
proxy_command_prefix=" -o 'ProxyCommand=nc -X connect -x "

# Generate SSH command based on connection mode
case $connection_mode in
  pfwd)
    ssh_command="$base_ssh_command -N -L $local_port:$instance_ip:$target_port"
    ;;
  socks)
    ssh_command="$base_ssh_command -N -D 127.0.0.1:$local_port"
    ;;
  *)
    ssh_command="$base_ssh_command -o ProxyCommand=\"ssh -i $ssh_private_key -W %h:%p -p 22 $session_id@host.bastion.$region.oci.oraclecloud.com"
    ;;
esac

# Add proxy settings if HTTP proxy is set
if [ -n "$http_proxy" ]; then
  ssh_command+="$proxy_command_prefix$http_proxy %h %p'"
fi

# Append rest of the SSH command
if [ "$connection_mode" = "pfwd" ] || [ "$connection_mode" = "socks" ] ; then
    ssh_command+=" -p 22 $bastion_user_name@host.bastion.$region.oci.oraclecloud.com"
else
    ssh_command+="\" -p 22 $bastion_user_name@$instance_ip"
fi

# Print final SSH command
echo "$ssh_command"

# Special instructions for SOCKS mode
if [ "$connection_mode" = "socks" ]; then
  echo
  echo "Then you can connect any resources in subnet using IP or FQDN using the following command:"
  echo ""
  echo "ssh -o 'ProxyCommand=nc -x connect -x 127.0.0.1:$local_port %h %p' -p 22 <username>@<IP or FQDN>:<port>"
fi
echo
}

main() {
    # Check dependencies
    check_dependencies

    # Parse command line options
    while getopts "hr:i:p:u:l:s:" option; do
        case ${option} in
            h) Help ;;
            i) instance_ip=$OPTARG ;;
            r) target_port=$OPTARG
               connection_mode="pfwd" ;;
            u) target_os_username=$OPTARG ;;
            p) profile=$OPTARG ;;
            l) local_port=$OPTARG ;;
            s) subnet_id=$OPTARG
               connection_mode="socks" ;;
            :) error_exit "Option -$OPTARG requires an argument" ;;
            ?) error_exit "Invalid option -$OPTARG" ;;
        esac
    done
    
    shift $((OPTIND-1))
    if [ "$*" ]; then
        instance_ocid=$*
    fi
    
    # Validate required parameters
    [ -z "${instance_ocid}" ] && [ -z "${instance_ip}" ] && [ -z "${subnet_id}" ] && error_exit "Either instance/subnet OCID or IP must be provided"
    grep -q "\[$profile\]" "$HOME"/.oci/config || error_exit "Profile name cannot be found in ${HOME}/.oci/config"

    # Main workflow
    check_ssh_keys
    check_bastion_plugin
    
    log "INFO" "Checking for existing Bastion service..."
    
    # Get subnet information
    local privateip_ocid get_subnet subnet_name subnet_compartment_id vcn_id
    if [ -z "${instance_ocid}" ] && [ -z "${subnet_id}" ]; then
        privateip_ocid=$(oci search resource free-text-search --text "${instance_ip}" --profile "${profile}" | \
            jq -r '.data.items[] | select (.identifier | test("^ocid1.privateip")) | .identifier') || error_exit "Failed to find private IP"
        subnet_id=$(oci network private-ip get --private-ip-id "${privateip_ocid}" --profile "${profile}" | \
            jq -r '.data."subnet-id"') || error_exit "Failed to get subnet ID"
    elif [ -z "${subnet_id}" ]; then
        subnet_id=$(oci compute instance list-vnics --instance-id "${instance_ocid}" --profile "${profile}" | \
            jq -r '.data[0]."subnet-id"') || error_exit "Failed to get subnet ID"
    fi
    
    get_subnet=$(oci network subnet get --subnet-id "${subnet_id}" --profile "${profile}") || error_exit "Failed to get subnet details"
    subnet_name=$(echo -E "${get_subnet}" | jq -r '.data."display-name"')
    subnet_compartment_id=$(echo -E "${get_subnet}" | jq -r '.data."compartment-id"')
    vcn_id=$(echo -E "${get_subnet}" | jq -r '.data."vcn-id"')
    
    # Check for existing bastion
    bastion_id=$(oci bastion bastion list -c "${subnet_compartment_id}" --all --profile "${profile}" | \
        jq -r --arg SUBNET_ID "${subnet_id}" \
        'first(.data[]|select(."lifecycle-state" == "ACTIVE" and ."target-subnet-id"==$SUBNET_ID)|.id)')
    
    if [ -z "${bastion_id}" ]; then
        bastion_id=$(oci bastion bastion list -c "${subnet_compartment_id}" --all --profile "${profile}" | \
            jq -r --arg VCN_ID "${vcn_id}" \
            'first(.data[]|select(."lifecycle-state" == "ACTIVE" and ."target-vcn-id"==$VCN_ID)|.id)')
    fi
    
    if [ -z "${bastion_id}" ]; then
        log "WARNING" "Bastion service not present for this subnet (in subnet compartment)"
        read -rp "Do you want to create it? (Y/N) " -n 1 reply
        echo
        if [[ ! ${reply} =~ ^[Yy]$ ]]; then
            error_exit "Bastion creation cancelled"
        fi
        create_bastion_service "${subnet_name}" "${subnet_compartment_id}" "${subnet_id}"
    else
        log "SUCCESS" "Bastion service found"
    fi
    
    # Get instance IP if not provided
    if [ -n "${instance_ocid}" ] && [ -z "${instance_ip}" ]; then
        instance_ip=$(oci compute instance list-vnics --instance-id "${instance_ocid}" --profile "${profile}" | \
            jq -r '.data[]."private-ip"') || error_exit "Failed to get instance IP"
    fi
    
    create_session
}

# Check if script is being run with arguments
[[ $# -lt 1 ]] && Help 

# Run main function
main "$@"
