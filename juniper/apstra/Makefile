# ------------------------------------------------------------------------------
# Juniper Apstra - vrnetlab / srl-labs Makefile
#
# Place your Apstra qcow2 image (e.g. aos_server_6.1.1-70.qcow2) in this
# directory, then run:
#
#   make
#
# The resulting image will be tagged:
#   vrnetlab/juniper_apstra:<VERSION>
#
# To push to a custom registry set DOCKER_REGISTRY, e.g.:
#   DOCKER_REGISTRY=myregistry.example.com:5000/vrnetlab make docker-push
# ------------------------------------------------------------------------------

VENDOR        = Juniper
NAME          = Apstra

IMAGE_FORMAT  = qcow2

# Matches filenames like:
#   aos_server_5.0.0-335.qcow2
#   aos_server_6.1.1-70.qcow2
IMAGE_GLOB    = aos_server*.$(IMAGE_FORMAT)

# Extract the version from the filename.
# Strips the leading "aos_server_" prefix and the trailing ".qcow2" suffix.
# Result for "aos_server_6.1.1-70.qcow2" -> "6.1.1-70"
VERSION       = $(shell echo $(IMAGE) \
                  | sed -e 's/^aos_server_//' \
                  | sed -e 's/\.$(IMAGE_FORMAT)$$//')

# Pull in the shared srl-labs build machinery.
# These files live at the root of the vrnetlab checkout.
-include ../../makefile-sanity.include
-include ../../makefile.include
