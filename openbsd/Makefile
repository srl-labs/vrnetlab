VENDOR=OpenBSD
NAME=OpenBSD
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# openbsd-min_v7.7_2025-04-28-11-38.qcow2
VERSION=$(shell echo $(IMAGE) | sed -e 's/openbsd-min_v\([0-9]\.[0-9]\)_.*/\1/')

-include ../makefile-sanity.include
-include ../makefile.include

download:
	/bin/bash download.sh

build: download
	$(MAKE) docker-image
