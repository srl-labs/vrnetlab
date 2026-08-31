VENDOR=PLVision
NAME=sonic
IMAGE_FORMAT=qcow
IMAGE_GLOB=*.qcow2
#IMAGE=sonic-vs-lite.qcow2

# rename the disk image file as plvision-sonic-vs-<version>.qcow2
# examples:
# for a file named "plvision_sonic-vs-lite_1_13_202405.qcow2" the image will be named "vrnetlab/plvision_sonic:lite_1_13_202405"
VERSION=$(shell echo $(IMAGE) | sed -e 's/plvision_sonic-vs-//' | sed -e 's/.qcow2//')

-include ../../makefile-sanity.include
-include ../../makefile.include
