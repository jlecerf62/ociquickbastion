#/bin/sh

ssh_private_key="$HOME/.ssh/id_rsa"
ssh_public_key="$HOME/.ssh/id_rsa.pub"
bastion_id=""
is_agent_enabled=""
session_ttl="3600"
target_port="22"
target_os_username="opc"


echo "Searching for SSH key..."
if [ ! -f $ssh_private_key ] || [ ! -f $ssh_public_key ]; then
    echo "$HOME/.ssh/id_rsa not found"
    read -p "Do you want to generate a new RSA keypair ? (Y/N)" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        ssh-keygen -t rsa -f $HOME/.ssh/id_rsa -N ""
        echo
        echo "RSA keypair generated"
    fi
else 
    echo "$HOME/.ssh/id_rsa found"
fi

echo
echo "Detecting Bastion plugin state..."
get_instance=$(oci compute instance get --instance-id $1)
instance_name=$(echo $get_instance | jq -r '.data."display-name"')
instance_compartment_id=$(echo $get_instance | jq -r '.data."compartment-id"')
is_agent_enabled=$(oci instance-agent plugin get --instanceagent-id $1 -c $instance_compartment_id --plugin-name Bastion | jq -r '.data|select(.status=="RUNNING")')

if [ -z "$is_agent_enabled" ]
then
    echo "Bastion plugin is not enabled on $instance_name."
    echo "Please enable bastion plugin and retry later."
    exit 1
fi
echo "Bastion plugin is in RUNNING state on instance $instance_name."
echo 
echo "Checking for existing Bastion service..."
subnet_id=$(oci compute instance list-vnics --instance-id $1 | jq -r '.data[0]."subnet-id"')
get_subnet=$(oci network subnet get --subnet-id $subnet_id)
subnet_name=$(echo $get_subnet | jq -r '.data."display-name"')
subnet_compartment_id=$(echo $get_subnet | jq -r '.data."compartment-id"')
bastion_id=$(oci bastion bastion list -c $subnet_compartment_id --all | jq -r --arg SUBNET_ID "$subnet_id" 'select (.data[]."target-subnet-id" == $SUBNET_ID) | select (.data[]."lifecycle-state" == "ACTIVE") | .data[0].id')

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
    echo "Creating Bastion QuickBastion$subnet_name..."
    oci bastion bastion create --bastion-type "standard" -c $subnet_compartment_id --target-subnet-id $subnet_id --name "QuickBastion$subnet_name" --client-cidr-list '["0.0.0.0/0"]' --wait-for-state "SUCCEEDED" | jq -r '.data.id'
    bastion_id=$(oci bastion bastion list -c $subnet_compartment_id --all | jq -r --arg SUBNET_ID "$subnet_id" 'select (.data[]."target-subnet-id" == $SUBNET_ID) | select (.data[]."lifecycle-state" == "ACTIVE") | .data[0].id')
fi

echo "Bastion service found."
echo "Creating session..."
instance_ip=$(oci compute instance list-vnics --instance-id $1 | jq -r '.data[]."private-ip"')
session_id=$(oci bastion session create-managed-ssh --bastion-id $bastion_id --ssh-public-key-file=$ssh_public_key --target-os-username=$target_os_username --target-port=$target_port --target-resource-id=$1 --target-private-ip=$instance_ip --session-ttl $session_ttl --wait-for-state "SUCCEEDED" | jq -r '.data.resources[].identifier')
ssh_command=$(oci bastion session get --session-id $session_id | jq -r '.data."ssh-metadata".command')
ssh_command=$(sed 's=<privateKey>='"$ssh_private_key"'=g' <<< $ssh_command)
echo
echo "Type the following SSH command to connect instance"
echo 
echo $ssh_command
echo