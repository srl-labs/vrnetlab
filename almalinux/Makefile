VENDOR=Almalinux
NAME=Almalinux
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2
VERSION=10


-include ../makefile-sanity.include
-include ../makefile.include

download:
	/bin/bash download.sh

build:  download 
	$(MAKE) docker-image
