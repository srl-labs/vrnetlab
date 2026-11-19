VENDOR=Siderolabs
NAME=Talos
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

VERSION=$(shell echo $(IMAGE) | sed -e 's/\(.\+\)-metal-amd64.*/\1/')

-include ../makefile-sanity.include
-include ../makefile.include

download:
	/bin/bash download.sh

build: download
	$(MAKE) docker-image
