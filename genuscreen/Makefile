VENDOR=Genua
NAME=genuscreen
IMAGE_FORMAT=iso
IMAGE_GLOB=*.iso

# match versions like:
# genuscreen-8.0.iso
# genuscreen-1.0.0.iso
# genuscreen-2.1.3.iso
VERSION=$(shell echo $(IMAGE) | sed -E 's/.*genuscreen-([0-9]+\.[0-9]+(\.[0-9]+)?)\.iso/\1/')

# Check if ISO files exist
ISO_FILES=$(wildcard $(IMAGE_GLOB))
ifeq ($(ISO_FILES),)
$(error No ISO files found! Please place genuscreen ISO files (e.g., genuscreen-8.0.iso) in this directory)
endif

-include ../makefile-sanity.include
-include ../makefile.include
-include ../makefile-install.include