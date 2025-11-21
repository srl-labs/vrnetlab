VENDOR=Canonical
NAME=Ubuntu
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# vpp-proto-bookworm-20250607
VERSION=vpp-proto-bookworm-20250607

-include ../makefile-sanity.include
-include ../makefile.include

download:
	/bin/bash download.sh

build: download
	$(MAKE) docker-image
