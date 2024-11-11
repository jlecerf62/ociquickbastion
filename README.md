# OCI quickbastion

OCI Bastion service can sometimes be difficult and long to setup.
Here is a small script to automate Bastion session creation.

## Requirements

You need to have OCI-CLI installed and configured on your computer.

You also need to install **jq** package.

Or you can use OCI Cloud Shell which already have all the prerequisites.

You need also to have at least the following IAM permissions.

        Allow group <group> to read virtual-network-family in compartment <compartment>
        Allow group <group> to read instance-family in compartment <compartment>
        Allow group <group> to inspect work-requests in tenancy
        Allow group <group> to inspect compartments in tenancy
        Allow group <group> to use bastion in compartment <compartment>
        Allow group <group> to manage bastion-session in compartment <compartment>
        Allow group <group> to read bastion in compartment <compartment>

## Installation

        git clone https://github.com/jlecerf62/ociquickbastion.git

## Usage

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

## Example

                jerome@cloudshell:ociquickbastion (eu-frankfurt-1)$ ./quickbastion.sh ocid1.instance.oc1.eu-frankfurt-1.xxxxxxxxxxxxxxxxxxxxxxx
                
                Searching for SSH key...
                /home/jerome/.ssh/id_rsa not found
                
                Do you want to generate a new RSA keypair ? (Y/N)y
                Generating public/private rsa key pair.
                Created directory '/home/jerome/.ssh'.
                Your identification has been saved in /home/jerome/.ssh/id_rsa.
                Your public key has been saved in /home/jerome/.ssh/id_rsa.pub.
                The key fingerprint is:
                SHA256:0oyKkVXjxxxxxxxxxxxxxx jerome@be0f89axxxx
                The key's randomart image is:
                +---[RSA 2048]----+
                | o..o==.         |
                |  =ooo.o         |
                |  o=...          |
                | .oo= .+         |
                |. oO Eo.S        |
                |.xxxxxxxxxx      |
                | +.@.o ...       |
                |. B o  ....      |
                |=o   ..   ..     |
                +----[SHA256]-----+

                RSA keypair generated

                Detecting Bastion plugin state...
                Bastion plugin is in RUNNING state on instance instance-20220509-2224.

                Checking for existing Bastion service...
                Bastion service not present for this subnet (in subnet compartment).
                Do you want to create it? (Y/N)y

                Creating Bastion QuickBastionsubnet1... Please wait, it can take up to 2 minutes...
                Action completed. Waiting until the work request has entered state: ('SUCCEEDED',)
                
                Creating session... Please wait, it can take up to 2 minutes...
                Action completed. Waiting until the work request has entered state: ('SUCCEEDED',)
                Session has been created. Session Lifetime is 3600 seconds

                Type the following SSH command to connect instance

                ssh -i /home/jerome/.ssh/id_rsa -o ProxyCommand="ssh -i /home/jerome/.ssh/id_rsa -W %h:%p -p 22 ocid1.bastionsession.oc1.eu-frankfurt-1.axxxxxxxxxxxxxxxxxxxxxxxx@host.bastion.eu-frankfurt-1.oci.oraclecloud.com" -p 22 opc@192.168.250.10
