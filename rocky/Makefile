VENDOR=Rocky
NAME=Rocky
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# 9-rocky-cloud.qcow2
VERSION=$(shell echo $(IMAGE) | sed -e 's/\([0-9]\+\)-rocky-cloud.*/\1/')


-include ../makefile-sanity.include
-include ../makefile.include

download:
	/bin/bash download.sh

build: download
	$(MAKE) docker-image
