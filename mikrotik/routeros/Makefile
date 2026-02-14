VENDOR=Mikrotik
NAME=RouterOS
IMAGE_FORMAT=vmdk/vdi
IMAGE_GLOB=*.vmdk *.vdi

# match versions like:
# chr-6.39.2.vmdk -> 6.39.2-amd64
# chr-7.20.4-arm64.vdi -> 7.20.4-arm64
# VERSION always includes platform suffix, defaulting to -amd64 if not present
VERSION=$(shell echo $(IMAGE) | sed -rn 's/.*chr-(.+)\.(vmdk|vdi)/\1/p' | sed -r 's/$$/-amd64/' | sed -r 's/-arm64-amd64$$/-arm64/')

# Detect target platform from image filename
# arm64 images have -arm64 in the filename, otherwise assume amd64
DOCKER_PLATFORM=$(shell if echo "$(VERSION)" | grep -q '\-arm64'; then echo "linux/arm64"; else echo "linux/amd64"; fi)

# Detect host platform
HOST_ARCH=$(shell uname -m)
HOST_PLATFORM=$(shell if [ "$(HOST_ARCH)" = "aarch64" ] || [ "$(HOST_ARCH)" = "arm64" ]; then echo "arm64"; else echo "amd64"; fi)

# Extract base version without platform suffix for default tag
VERSION_BASE=$(shell echo "$(VERSION)" | sed -r 's/-(amd64|arm64)$$//')

-include ../../makefile-sanity.include
-include ../../makefile.include

# Add default tag (without platform suffix) only if image platform matches host platform
docker-build-extra:
	@if echo "$(VERSION)" | grep -q '\-$(HOST_PLATFORM)'; then \
		echo "Adding default tag $(VERSION_BASE) pointing to $(VERSION) (matches host platform: $(HOST_PLATFORM))"; \
		docker tag $(IMG_REPOSITORY):$(VERSION) $(IMG_REPOSITORY):$(VERSION_BASE); \
	else \
		echo "Skipping default tag $(VERSION_BASE) (image platform doesn't match host platform: $(HOST_PLATFORM))"; \
	fi

docker-push-image-extra:
	@if echo "$(VERSION)" | grep -q '\-$(HOST_PLATFORM)'; then \
		echo "Pushing default tag $(VERSION_BASE)"; \
		docker push $(IMG_REPOSITORY):$(VERSION_BASE); \
	fi
