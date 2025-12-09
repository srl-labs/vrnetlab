# vrnetlab / Mikrotik RouterOS (ROS)

This is the vrnetlab docker image for Mikrotik RouterOS (ROS).

## Building the docker image
Download the Cloud Hosted Router (CHR) VMDK or arm64 VDI image from https://www.mikrotik.com/download
Copy the vmdk image into this folder, then run `make docker-image`.

### Cross platform builds

It is possible to build amd64 images on ARM64 Macs, due to the built in Rosetta 2 emulation.

For building arm64 images on amd64 machines please resort to:
https://docs.docker.com/build/building/multi-platform/#strategies


Tested booting and responding to SSH:
 * chr-6.39.2.vmdk   MD5:eb99636e3cdbd1ea79551170c68a9a27
 * chr-6.47.9.vmdk
 * chr-7.1beta5.vmdk
 * chr-7.16.2.vmdk
 * chr-7.20.4-arm64.vdi


## System requirements
CPU: 1 core

RAM: <1GB

Disk: <1GB

On Apple ARM64 systems a CPU with nested virtualization support is needed. (M3 or greater)

## Containerlab
Containerlab kind for routeros is [mikrotik_ros](https://containerlab.dev/manual/kinds/vr-ros/).
