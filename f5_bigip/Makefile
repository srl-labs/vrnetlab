VENDOR=F5
NAME=BIGIP-VE
IMAGE_FORMAT=qcow2
IMAGE_GLOB=BIGIP*.qcow2

# Derive VERSION from the first matching qcow2, unless explicitly set.
# Examples:
#   BIGIP-17.5.1-0.0.7.qcow2            -> 17.5.1-0.0.7
#   BIGIP-17.1.1.3-0.0.12.ALL.qcow2     -> 17.1.1.3-0.0.12
VERSION ?= $(shell \
  f=$$(basename $$(ls -1 $(IMAGE_GLOB) 2>/dev/null | head -n1)); \
  test -n "$$f" && echo "$$f" | \
    sed -E 's/\.qcow2$$//I; s/^[^-]+-//; s/\.all$$//I' || echo "local" \
)

-include ../makefile-sanity.include
-include ../makefile.include
