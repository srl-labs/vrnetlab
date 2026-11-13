# vrnetlab / IPInfusion OcNOS

This is the vrnetlab docker image for IPInfusion OcNOS.

## Building the docker image

Download the OcNOS VM image for KVM from IP Infusion (free, registration required):
https://www.ipinfusion.com/free-software-demos/?vm=KVM

Decompress it (`xz -d`), copy the qcow2 image into this folder, then run `make docker-image`.

Tested booting and responding to Telnet:

- OcNOS-SP-PLUS-x86-7.0.0-262-GA.qcow2 MD5:8c6f3a35c431924149fb43143ae35a62
- OcNOS-SP-PLUS-x86-6.5.2-101-GA.qcow2 MD5:796c121be77d43ffffbf6214a44f54eb

Tested booting and responding to SSH:  
(The relevant parts of the Makefile for this version are commented out.)

- DEMO_VM-OcNOS-6.0.2.11-MPLS-x86-MR.qcow2 MD5:08bbaf99347c33f75d15f552bda762e1

## Serial console issues

(This issue did not occur in version 6.5.2-101.)  
The image of OcNOS version 6.0.2.11 distributed from the official website has a bug that prevents connection via serial console.
This problem can be corrected by modifying /boot/grub/grub.cfg in the image.

For example, it can be modified as follows

```
OCNOS_IMAGE="DEMO_VM-OcNOS-6.0.2.11-MPLS-x86-MR.qcow2"

modprobe nbd
qemu-nbd --connect=/dev/nbd0 $OCNOS_IMAGE
mkdir -p /tmp/OCNOS_ROOT
mount /dev/nbd0p1 /tmp/OCNOS_ROOT
sed -ie 's/\( *linux.*\)$/\1 console=ttyS0,115200n8/' /tmp/OCNOS_ROOT/boot/grub/grub.cfg
umount /tmp/OCNOS_ROOT
qemu-nbd --disconnect /dev/nbd0
```

## System requirements

CPU: 2 core

RAM: <4GB

Disk: <4GB

## Containerlab

Containerlab kind for OcNOS is [ipinfusion_ocnos](https://containerlab.dev/manual/kinds/ipinfusion-ocnos/).
