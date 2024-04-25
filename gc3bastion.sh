#!/bin/bash
#variables
ssh_private_key="$HOME/.ssh/id_rsa"
ssh_public_key="$HOME/.ssh/id_rsa.pub"
bastion_id=""
is_agent_enabled=""
session_ttl="10800"
target_port="22"
local_port=""
target_os_username="opc"
connection_mode="ssh"
instance_ip=""
instance_ocid=""
OPTIND=1
profile="GC3" 
proxy_list='["141.143.193.64/27","148.87.23.0/27","137.254.7.160/27","129.157.69.32/27","209.17.40.32/27","209.17.37.96/27","198.49.164.160/27","141.143.213.32/27","196.15.23.0/27","192.188.170.80/28","202.45.129.176/28","202.92.67.176/29"]'


Help()
{
   # Display Help
   echo "Create OCI BASTION session."
   echo
   echo "./quickbastion.sh [-h|i|p|u|r] <instance ocid>"
   echo
   echo "options:"
   echo "-h     Print this Help."
   echo "-i     Instance IP (port-forwarding)."
   echo "-r     Remote tcp port (port-forwarding)."
   echo "-u     Remote username (default opc)."
   echo "-p     OCI-CLI config profile (optional)."
   echo "-l     local tcp port (port-forwarding)"
   exit 1
}

[[ $# -lt 1 ]] && Help 

while getopts "hr:i:p:u:l:" option; do
   case $option in
      h) # display Help
         Help
         # shellcheck disable=SC2317
         exit;;
      i) # instance IP for port forwarding
         instance_ip=$OPTARG
         connection_mode="pfwd";;
      r) # remote port for port forwarding
         target_port=$OPTARG
         connection_mode="pfwd";;
      u) # username
         target_os_username=$OPTARG;;
      p) # OCI-CLI config profile
         profile=$OPTARG;;
      l) # local port for port forwarding
         local_port=$OPTARG;;
      :)
         echo "Option $OPTARG required argument" >&2 
         exit;;
     \?) # Invalid option
         echo "Error: Invalid option" >&2
         exit;;
   esac
done
shift "$((OPTIND-1))"
if [ "$*" ]
then
    instance_ocid=$*
fi

echo
echo "Searching for SSH key..."
if [ ! -f "$ssh_private_key" ] || [ ! -f "$ssh_public_key" ]; then
    echo "$HOME/.ssh/id_rsa not found"
    read -p "Do you want to generate a new RSA keypair ? (Y/N)" -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ssh-keygen -t rsa -f "$HOME"/.ssh/id_rsa -N ""
        echo
        echo "RSA keypair generated"
    fi
else 
    echo "$HOME/.ssh/id_rsa found"
    echo
fi

if [ $connection_mode = "ssh" ]; then
    echo
    echo "Detecting Bastion plugin state..."
    get_instance=$(oci compute instance get --instance-id "$instance_ocid" --profile "$profile")
    instance_name=$(echo -E "$get_instance" | jq -r '.data."display-name"')
    instance_compartment_id=$(echo -E "$get_instance" | jq -r '.data."compartment-id"')
    is_agent_enabled=$(oci instance-agent plugin get --instanceagent-id "$instance_ocid" -c "$instance_compartment_id" --plugin-name Bastion --profile "$profile" | jq -r '.data|select(.status=="RUNNING")')
    if [ -z "$is_agent_enabled" ]; then
        echo "Bastion plugin is not enabled on $instance_name."
        echo "Please enable bastion plugin and retry later."
        exit 1
    else
        echo "Bastion plugin is in RUNNING state on instance $instance_name."
        echo
    fi
fi
echo "Checking for existing Bastion service..."
if [ -z "$instance_ocid" ]
then
    privateip_ocid=$(oci search resource free-text-search --text "$instance_ip" --profile "$profile" | jq -r '.data.items[] | select (.identifier | test("^ocid1.privateip")) | .identifier')
    ## TODO: check that cmd return only 1 element
    subnet_id=$(oci network private-ip get --private-ip-id "$privateip_ocid" --profile "$profile" | jq -r '.data."subnet-id"')
else
    subnet_id=$(oci compute instance list-vnics --instance-id "$instance_ocid" --profile "$profile" | jq -r '.data[0]."subnet-id"')
fi
get_subnet=$(oci network subnet get --subnet-id "$subnet_id" --profile "$profile")
subnet_name=$(echo -E "$get_subnet" | jq -r '.data."display-name"')
subnet_compartment_id=$(echo -E "$get_subnet" | jq -r '.data."compartment-id"')
vcn_id=$(echo -E "$get_subnet" | jq -r '.data."vcn-id"')
bastion_id=$(oci bastion bastion list -c "$subnet_compartment_id" --all --profile "$profile" | jq -r --arg SUBNET_ID "$subnet_id" 'first(.data[]|select(."lifecycle-state" == "ACTIVE" and ."target-subnet-id"==$SUBNET_ID)|.id)')
if [ -z "$bastion_id" ]
then
    bastion_id=$(oci bastion bastion list -c "$subnet_compartment_id" --all --profile "$profile" | jq -r --arg VCN_ID "$vcn_id" 'first(.data[]|select(."lifecycle-state" == "ACTIVE" and ."target-vcn-id"==$VCN_ID)|.id)')
fi

if [ -z "$bastion_id" ]
then
    echo "Bastion service not present for this subnet (in subnet compartment)."
    read -p "Do you want to create it? (Y/N)" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]
    then
        echo "Bastion creation cancelled."
        echo
        exit 1
    fi
    echo 
    ## TODO: check if bastion lenght reach max lenght name
    echo "Creating Bastion QuickBastion$subnet_name... Please wait, it can take up to 2 minutes..."
    oci bastion bastion create --bastion-type "standard" -c "$subnet_compartment_id" --target-subnet-id "$subnet_id" --name "QuickBastion$subnet_name" --client-cidr-list "$proxy_list" --wait-for-state "SUCCEEDED" --profile "$profile" | jq -r '.data.id'
    bastion_id=$(oci bastion bastion list -c "$subnet_compartment_id" --all --profile "$profile" | jq -r --arg SUBNET_ID "$subnet_id" 'first(.data[]|select(."lifecycle-state" == "ACTIVE" and ."target-subnet-id" == $SUBNET_ID)|.id)')
fi
echo "Bastion service found."
echo


echo "Creating session... Please wait, it can take up to 2 minutes..."
if [ "$instance_ocid" ] && [ ! "$instance_ip" ]
then
    instance_ip=$(oci compute instance list-vnics --instance-id "$instance_ocid" --profile "$profile" | jq -r '.data[]."private-ip"')
fi
if [ $connection_mode = "pfwd" ]
then
    session_id=$(oci bastion session create-port-forwarding --bastion-id "$bastion_id" --ssh-public-key-file="$ssh_public_key" --target-port="$target_port" --target-private-ip="$instance_ip" --session-ttl=$session_ttl --wait-for-state "SUCCEEDED" --profile "$profile" | jq -r '.data.resources[].identifier')
else
    session_id=$(oci bastion session create-managed-ssh --bastion-id "$bastion_id" --ssh-public-key-file="$ssh_public_key" --target-os-username="$target_os_username" --target-port="$target_port" --target-resource-id="$instance_ocid" --target-private-ip="$instance_ip" --session-ttl=$session_ttl --wait-for-state "SUCCEEDED" --profile "$profile" | jq -r '.data.resources[].identifier')
fi
bastion_user_name=$(oci bastion session get --session-id "$session_id" --profile "$profile" | jq -r '.data."bastion-user-name"')
echo "Session has been created. Session Lifetime is $session_ttl seconds"
echo
echo "Type the following SSH command to connect instance: "
echo 
if [ $connection_mode = "pfwd" ]
then
    echo "ssh -i $ssh_private_key -N -L $local_port:$instance_ip:$target_port -o 'ProxyCommand=nc -X connect -x www-proxy-ams.nl.oracle.com:80 %h %p' -p 22 $bastion_user_name@host.bastion.eu-paris-1.oci.oraclecloud.com"
else
    echo "ssh -i $ssh_private_key -o ProxyCommand=\"ssh -i $ssh_private_key -W %h:%p -p 22 $session_id@host.bastion.eu-paris-1.oci.oraclecloud.com -o 'ProxyCommand=nc -X connect -x www-proxy-ams.nl.oracle.com:80 %%h %%p'\" -p 22 $bastion_user_name@$instance_ip"
fi
echo
