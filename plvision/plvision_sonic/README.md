# Enterprise SONiC Linux Distribution by PLVision

This directory provides a vrnetlab wrapper for running [Enterprise SONiC Linux Distribution by PLVision](https://plvision.eu/sonic-lite) as a Containerlab-compatible virtual network operating system. 

## Overview 
`Enterprise SONiC Linux Distribution by PLVision` is an optimized SONiC distribution designed for:

- Access switches
- Out-of-band management switches
- Campus deployments
- Edge deployments
- Resource-constrained platforms

The virtual appliance is packaged as a QEMU virtual machine and executed inside a Docker container through vrnetlab.

## Building the docker image

To download the image, log in to [Solutions: PLVision ServiceDesk](https://support.plvision.eu/support/solutions) and find the required version.

Extract the archive and place the `.img` file in this directory. Rename the file to `plvision_sonic-vs-<version>.qcow2` and run `make`.

After typing `make`, a new image will appear named `vrnetlab/plvision_sonic:<version>`.

Run `docker images` to confirm this.

## System requirements

- CPU: 2 cores
- RAM: 4GB
- DISK: ~6.5GB

## Configuration

SONiC nodes boot with a basic configuration by default, enabling SSH and basic management connectivity. The factory-default configuration is retained.
Full startup configuration can be passed by mounting it under `/config/config_db.json`; Containerlab does this automatically. Only the SONiC JSON config format is accepted. This file replaces the existing default config.
