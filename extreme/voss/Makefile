VENDOR=Extreme
NAME=VOSS
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# FEGNS3.9.3.1.0.qcow2 -> 9.3.1.0
VERSION=$(shell echo $(IMAGE) | sed -n 's/^\(FEGNS3\|VOSSGNS3\)\.\(.*\)\.qcow.*/\2/p' | cut -d'-' -f1)

-include ../../makefile-sanity.include
-include ../../makefile.include
