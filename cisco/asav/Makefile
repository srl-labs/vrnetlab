VENDOR=Cisco
NAME=ASAv
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# asav9-18-2.qcow2
VERSION=$(shell echo $(IMAGE) | sed -e 's/.*asav\([0-9].*\)\.qcow2/\1/')

-include ../../makefile-sanity.include
-include ../../makefile.include
