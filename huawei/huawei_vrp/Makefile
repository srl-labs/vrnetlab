VENDOR=Huawei
NAME=VRP
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# huawei_<platform>-<any version string>.qcow2
# where platform is one of ne40e, ne5000e, ne9000, cx, ce12800
VERSION=$(shell echo $(IMAGE) | sed -e 's/huawei_\(ne40e\|ne5000e\|ne9000\|cx\|ce12800\)-\(.*\)\.qcow2/\1-\2/')

# eNSP images are converted to qcow2 before the build, see README.md
ENSP_IMAGES=$(wildcard *.zip *.vdi)
ENSP_STAMPS=$(patsubst %,.%.converted,$(ENSP_IMAGES))

.DEFAULT_GOAL := build

-include ../../makefile-sanity.include
-include ../../makefile.include

.PHONY: build ensp-images

build: ensp-images
	@$(MAKE) --no-print-directory docker-image

ensp-images: $(ENSP_STAMPS)

.%.converted: %
	./ensp/convert.sh $< .
	touch $@
