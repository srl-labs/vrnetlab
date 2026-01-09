VENDOR=IPInfusion
NAME=OcNOS
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# DEMO_VM-OcNOS-6.0.2.11-MPLS-x86-MR.qcow2
#VERSION=$(shell echo $(IMAGE) | sed -rn 's/DEMO_VM-OcNOS-(.+)-MPLS-.*.qcow/\1/p')

# match versions like:
# OcNOS-SP-PLUS-x86-6.5.2-101-GA.qcow2
#VERSION=$(shell echo $(IMAGE) | sed -rn 's/OcNOS-SP-PLUS-x86-(.+)-GA.qcow2/\1/p')
VERSION=$(shell echo $(IMAGE) | sed -rn 's/OcNOS-SP-PLUS-x86-(.+)-(GA|MR).qcow2/\1/p')

-include ../../makefile-sanity.include
-include ../../makefile.include
