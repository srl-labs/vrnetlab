VENDOR=Cisco
# Detect NAME from image filename (cat9kv or c9800cl)
NAME=$(shell if echo $(IMAGE) | grep -qi "c9800"; then echo "c9800cl"; else echo "cat9kv"; fi)
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# match versions like:
# cat9kv_prd.17.12.01.qcow2
# C9800-CL-universalk9.17.15.04b.qcow2
VERSION=$(shell echo $(IMAGE) | sed -e 's/.*[^0-9]\([0-9]\+\.[0-9]\+\.[0-9]\+[a-z0-9]*\).*/\1/')

-include ../../makefile-sanity.include
-include ../../makefile.include
