VENDOR=OPNsense
NAME=OPNsense
IMAGE_FORMAT=qcow2
IMAGE_GLOB=OPNsense-*.qcow2

# match versions like: OPNsense-26.1.6.qcow2
VERSION=$(shell echo $(IMAGE) | sed -e 's/OPNsense-\([0-9]\+\.[0-9]\+\.[0-9]\+\)\.qcow2/\1/')

-include ../makefile-sanity.include
-include ../makefile.include

# Also publish a clean vrnetlab/opnsense:<version> tag (alias of the
# vendor_name tag the include produces) for use with containerlab's
# generic_vm kind.
docker-build-extra:
	docker tag $(IMG_REPOSITORY):$(VERSION) vrnetlab/opnsense:$(VERSION)

build: docker-image
