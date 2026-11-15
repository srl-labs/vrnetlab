VENDOR=VeloCloud
IMAGE_FORMAT=qcow2/tar
IMAGE_GLOB=edge-VC_KVM_GUEST-*.qcow2 velocloud-vcc-*.qcow2 velocloud-orchestrator-kvm-*.tar

# Detect the component from the image filename:
#   edge-VC_KVM_GUEST-x86_64-6.4.4.0-...-updatable-ext4.qcow2    -> vce, 6.4.4.0
#   velocloud-vcc-v3-6.4.4.0-...-kvm.qcow2                       -> vcc, 6.4.4.0
#   velocloud-orchestrator-kvm-6.4.2.13-R64213-...-b778364712.tar -> vco, 6.4.2.13
#     (the VCO tar holds rootfs.qcow2, store.qcow2, store2.qcow2, store3.qcow2)
COMPONENT=$(strip \
	$(if $(filter edge-VC_KVM_GUEST-%.qcow2,$(IMAGE)),vce,\
	$(if $(filter velocloud-vcc-%.qcow2,$(IMAGE)),vcc,\
	$(if $(filter velocloud-orchestrator-kvm-%.tar,$(IMAGE)),vco))))
VERSION=$(shell echo $(IMAGE) | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
NAME=$(COMPONENT)

-include ../../makefile-sanity.include
-include ../../makefile.include

docker-pre-build:
	@if [ -z "$(COMPONENT)" ]; then echo "ERROR: unknown VeloCloud image $(IMAGE)"; exit 1; fi

# VCO ships as a tar of several disks; extract it into the build context.
docker-build-image-copy:
ifeq ($(COMPONENT),vco)
	tar -xf $(IMAGE) -C docker/
else
	cp $(IMAGE) docker/
endif

vc-info:
	@echo "COMPONENT=$(COMPONENT) VERSION=$(VERSION) REPO=$(IMG_REPOSITORY)"
