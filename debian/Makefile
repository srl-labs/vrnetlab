VENDOR=Debian
NAME=Debian
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# debian-bookworm-genericcloud.qcow2
VERSION=$(shell echo $(IMAGE) | sed -e 's/debian-\([a-z]\+\)-genericcloud.*/\1/')


-include ../makefile-sanity.include
-include ../makefile.include

download:
	/bin/bash download.sh

build: download
	$(MAKE) docker-image
