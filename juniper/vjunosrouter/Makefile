VENDOR=Juniper
NAME=vJunos-router
IMAGE_FORMAT=qcow
IMAGE_GLOB=*.qcow2

# match versions like:
# vJunos-router-23.2R1.15.qcow2
# ...
VERSION=$(shell echo $(IMAGE) | sed -e 's/vJunos-router-//i' | sed -e 's/.qcow2//i')

.PHONY: enable-amd-svm-on-images all

all: enable-amd-svm-on-images
	$(MAKE) docker-image

-include ../../makefile-sanity.include
-include ../../makefile.include

IMAGES=$(shell ls $(IMAGE_GLOB) 2>/dev/null)

enable-amd-svm-on-images:
	docker pull ghcr.io/birbnetworks/vjunos-amd-enable:latest
	for IMAGE in $(IMAGES); do \
		echo "Enabling AMD SVM flags on $$IMAGE"; \
		docker run --rm \
			--cap-add SYS_ADMIN \
			--device /dev/kvm \
			-v "$$(pwd)/$$IMAGE:/vJunos.qcow2:rw" \
			ghcr.io/birbnetworks/vjunos-amd-enable:latest; \
	done
	docker rmi ghcr.io/birbnetworks/vjunos-amd-enable:latest || true
