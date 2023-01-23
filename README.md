# OCI quickbastion
OCI Bastion service can sometimes be difficult and long to setup.
Here is a small script to automate Bastion session creation.

## Requirements

You need to have OCI-CLI installed and configured on your computer.
You also need to install jq package.
Or you can use OCI Cloud Shell which already have all the prerequisites.

You need also to have at least the following IAM rights.

        allow <group> to read instances in compartment <compartment>
        allow <group> to read virtual-network-family in compartment <compartment>
        allow <group> to manage bastion-family in compartment <compartment>

## Installation

        git clone https://github.com/jlecerf62/ociquickbastion.git

## Usage

        ./quickbastion.sh <instance_ocid>

## Example

                jerome_lec@cloudshell:ociquickbastion (eu-frankfurt-1)$ ./quickbastion.sh ocid1.instance.oc1.eu-frankfurt-1.antheljrjqlnhlqcgharhv3yns4tufi7pmljj2ad5ghxxn3slwus5pnh7j4q
                
                Searching for SSH key...
                /home/jerome_lec/.ssh/id_rsa not found
                
                Do you want to generate a new RSA keypair ? (Y/N)y
                Generating public/private rsa key pair.
                Created directory '/home/jerome_lec/.ssh'.
                Your identification has been saved in /home/jerome_lec/.ssh/id_rsa.
                Your public key has been saved in /home/jerome_lec/.ssh/id_rsa.pub.
                The key fingerprint is:
                SHA256:0oyKkVXjwQGBw7SpPrMqXpYxwe40x0S67GPd94IK3IU jerome_lec@be0f89af1c14
                The key's randomart image is:
                +---[RSA 2048]----+
                | o..o==.         |
                |  =ooo.o         |
                |  o=...          |
                | .oo= .+         |
                |. oO Eo.S        |
                |. =oO.o.         |
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

                ssh -i /home/jerome_lec/.ssh/id_rsa -o ProxyCommand="ssh -i /home/jerome_lec/.ssh/id_rsa -W %h:%p -p 22 ocid1.bastionsession.oc1.eu-frankfurt-1.amaaaaaajqlnhlqanq2hwblnjtjetgweutzeq7gua7if77odij43yp6lvujq@host.bastion.eu-frankfurt-1.oci.oraclecloud.com" -p 22 opc@192.168.250.10
