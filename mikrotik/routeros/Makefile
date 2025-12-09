VENDOR=Mikrotik
NAME=RouterOS
IMAGE_FORMAT=vmdk/vdi
IMAGE_GLOB=*.vmdk *.vdi

# match versions like:
# chr-6.39.2.vmdk -> 6.39.2
# chr-7.20.4-arm64.vdi -> 7.20.4-arm64
VERSION=$(shell echo $(IMAGE) | sed -rn 's/.*chr-(.+)\.(vmdk|vdi)/\1/p')

# Detect target platform from image filename
# arm64 images have -arm64 in the filename, otherwise assume amd64
DOCKER_PLATFORM=$(shell if echo "$(VERSION)" | grep -q '\-arm64'; then echo "linux/arm64"; else echo "linux/amd64"; fi)

-include ../../makefile-sanity.include
-include ../../makefile.include

# Add -amd64 tag for amd64 images (for consistency with -arm64 tags)
# The base version tag is kept for backward compatibility
docker-build-extra:
	@if ! echo "$(VERSION)" | grep -q '\-arm64'; then \
		echo "Adding -amd64 tag for $(IMG_REPOSITORY):$(VERSION)"; \
		docker tag $(IMG_REPOSITORY):$(VERSION) $(IMG_REPOSITORY):$(VERSION)-amd64; \
	fi

docker-push-image-extra:
	@if ! echo "$(VERSION)" | grep -q '\-arm64'; then \
		echo "Pushing -amd64 tag for $(IMG_REPOSITORY):$(VERSION)"; \
		docker push $(IMG_REPOSITORY):$(VERSION)-amd64; \
	fi