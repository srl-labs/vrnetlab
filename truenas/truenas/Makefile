VENDOR=TrueNAS
NAME=TrueNAS
IMAGE_FORMAT=iso
IMAGE_GLOB=TrueNAS-*.iso

# match versions like:
# TrueNAS-27.0.0-RC.1.iso
# TrueNAS-25.10.1.iso
VERSION=$(shell echo $(IMAGE) | sed -e 's/TrueNAS-\(.*\)\.iso/\1/')

-include ../../makefile-sanity.include
-include ../../makefile.include

# the ISO is not copied into the image: the install step below mounts it read-only
docker-build-image-copy: ;

docker-pre-build:
	-cat cidfile | xargs --no-run-if-empty docker rm -f
	-rm -f cidfile

# boot the ISO's installer against the image's empty boot disk, then commit the installed disk into the image
docker-build: docker-build-common
	docker run --cidfile cidfile --privileged -v $(CURDIR)/$(IMAGE):/install.iso:ro $(REGISTRY)$(IMG_VENDOR)_$(IMG_NAME):$(VERSION) --trace --install
	docker commit --change='ENTRYPOINT ["uv", "run", "/launch.py"]' $$(cat cidfile) $(REGISTRY)$(IMG_VENDOR)_$(IMG_NAME):$(VERSION)
	docker rm -f $$(cat cidfile)
	rm -f cidfile
