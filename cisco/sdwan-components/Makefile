VENDOR=Cisco
NAME=sdwan-components
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# Detect component type and version from filename
# Examples:
# viptela_vmanage_20_16_1_gen.qcow2 -> manager, 20.16.1
# viptela_vsmart_20_16_1_gen.qcow2 -> controller, 20.16.1
# viptela_vbond_20_16_1_gen.qcow2 -> validator, 20.16.1
# viptela-edge-20.9.3-genericx86-64.qcow2 -> validator, 20.9.3
COMPONENT=$(shell echo $(IMAGE) | sed -n 's/.*[-_]v*\(manage\|smart\|bond\|edge\)[-_].*/\1/p' | sed -e 's/manage/manager/' -e 's/smart/controller/' -e 's/bond/validator/' -e 's/edge/validator/')
VERSION=$(shell echo $(IMAGE) | sed -n 's/.*\(manage\|smart\|bond\|edge\)[-_]\([0-9][0-9._]*[0-9]\).*/\2/p' | sed -e 's/_/./g')


-include ../../makefile-sanity.include
-include ../../makefile.include

# Override docker-build to include component type in image tag
docker-build: docker-build-common docker-clean-build

docker-build-common: docker-clean-build docker-pre-build
	@if [ -z "$$IMAGE" ]; then echo "ERROR: No IMAGE specified"; exit 1; fi
	@if [ "$(IMAGE)" = "$(VERSION)" ]; then echo "ERROR: Incorrect version string ($(IMAGE)). The regexp for extracting version information is likely incorrect, check the regexp in the Makefile or open an issue at https://github.com/hellt/vrnetlab/issues/new including the image file name you are using."; exit 1; fi
	@echo "Building docker image using $(IMAGE) as $(REGISTRY)$(IMG_VENDOR)_sdwan-$(COMPONENT):$(VERSION)"
	@if [ -d ../common ]; then \
		cp ../common/* docker/; \
	elif [ -d ../../common ]; then \
		cp ../../common/* docker/; \
	else \
		echo "ERROR: Cannot find common directory"; exit 1; \
	fi
	@[ -f ./vswitch.xml ] && cp vswitch.xml docker/ || true
	$(MAKE) IMAGE=$$IMAGE docker-build-image-copy
	(cd docker; docker build --build-arg http_proxy=$(http_proxy) --build-arg HTTP_PROXY=$(HTTP_PROXY) --build-arg https_proxy=$(https_proxy) --build-arg HTTPS_PROXY=$(HTTPS_PROXY) --build-arg IMAGE=$(IMAGE) --build-arg VERSION=$(VERSION) --label "vrnetlab-version=$(VRNETLAB_VERION)" -t $(REGISTRY)$(IMG_VENDOR)_sdwan-$(COMPONENT):$(VERSION) .)

build:
	@echo "Building all SD-WAN component images..."
	@for img in *.qcow2; do \
		if [ -f "$$img" ]; then \
			echo "Building $$img"; \
			$(MAKE) docker-image IMAGE=$$img; \
		fi \
	done