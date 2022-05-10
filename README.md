# OCI quickbastion
OCI Bastion service can sometimes be difficult and long to setup.
Here is a small script to automate Bastion session creation.

## Requirements

You need to have OCI-CLI installed and configured on your computer.
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