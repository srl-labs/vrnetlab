VENDOR=cisco
NAME=xrd-vrouter
IMAGE_FORMAT=tar
IMAGE_GLOB=*.tar

# Extract the version from the tarball name, e.g. xrd-vrouter-25.4.2.tar -> 25.4.2
VERSION=$(shell echo $(IMAGE) | sed -e 's/^xrd-vrouter-\(.*\)\.tar$$/\1/')

-include ../../makefile-sanity.include
-include ../../makefile.include

# The vendor artifact is the XRd vRouter *container* image (a `docker save`
# tarball), not a VM disk. It is passed to the install step via a bind mount
# rather than copied into an image layer, so it does not bloat the result.
docker-build-image-copy: ;

docker-pre-build:
	-cat cidfile 2>/dev/null | xargs --no-run-if-empty docker rm -f
	-rm -f cidfile

# Build the launcher image, then bake the golden micro-VM into it: a privileged
# run executes make-golden.sh, which provisions a fresh Ubuntu VM and preloads
# the XRd vRouter image; the result is committed back with the launcher entrypoint.
docker-build: docker-build-common
	docker run --cidfile cidfile --privileged --device /dev/kvm \
		-v "$(abspath $(IMAGE))":/xrd.tar:ro \
		--entrypoint /make-golden.sh \
		-e XRD_IMAGE_TAR=/xrd.tar \
		$(IMG_REPOSITORY):$(VERSION)
	docker commit --change='ENTRYPOINT ["uv", "run", "/launch.py"]' \
		$$(cat cidfile) $(IMG_REPOSITORY):$(VERSION)
	docker rm -f $$(cat cidfile)
	rm -f cidfile
