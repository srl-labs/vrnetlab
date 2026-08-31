# ------------------------------------------------------------------------------
# Nvidia Cumulus VX - vrnetlab / srl-labs Makefile
#
# Place your Cumulus VX qcow2 image (e.g. cumulus-linux-5.16.1-vx-amd64-qemu.qcow2)
# in this directory, then run:
#
#   make
#
# The resulting image will be tagged:
#   vrnetlab/nvidia_cumulus-vx:<VERSION>
#
# To push to a custom registry set DOCKER_REGISTRY, e.g.:
#   DOCKER_REGISTRY=myregistry.example.com:5000/vrnetlab make docker-push
# ------------------------------------------------------------------------------

VENDOR        = Nvidia
NAME          = Cumulus-VX

IMAGE_FORMAT  = qcow2

# Matches filenames like:
#   cumulus-linux-5.16.1-vx-amd64-qemu.qcow2
#   cumulus-linux-5.10.0-vx-amd64-qemu.qcow2
IMAGE_GLOB    = cumulus-linux*.$(IMAGE_FORMAT)

# Extract the version from the filename.
# Strips "cumulus-linux-" prefix and "-vx-amd64-qemu.qcow2" suffix.
# Result for "cumulus-linux-5.16.1-vx-amd64-qemu.qcow2" -> "5.16.1"
VERSION       = $(shell echo $(IMAGE) \
                  | sed -e 's/^cumulus-linux-//' \
                  | sed -e 's/-vx-amd64-qemu\.$(IMAGE_FORMAT)$$//')

# Pull in the shared srl-labs build machinery.
-include ../../makefile-sanity.include
-include ../../makefile.include
