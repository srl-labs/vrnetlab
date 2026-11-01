VENDOR=ALE
NAME=vAOS
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# The vAOS image is made of four files taken from the GNS3 appliance. Rename
# them with the same prefix, for example for 8.11.162.R01:
#   vaos-8.11.162.R01.qcow2              (vAOS8x.qcow2)
#   vaos-8.11.162.R01-ramdisk.gz         (vAOS8x_ramdisk.gz)
#   vaos-8.11.162.R01-bzImage            (vAOS8x_bzImage)
#   vaos-8.11.162.R01-chassis_info.tar   (cmm_chassis_info_vAOS8x.tar)
# match versions like 8.11.162.R01
VERSION=$(shell echo $(IMAGE) | sed -e 's/^vaos-//' -e 's/\.qcow2$$//')

-include ../../makefile-sanity.include
-include ../../makefile.include

# The common build only copies $(IMAGE)* into docker/. Copy the kernel,
# initrd and chassis information disk that come with the qcow2.
docker-pre-build:
	cp $(basename $(IMAGE))-ramdisk.gz $(basename $(IMAGE))-bzImage \
	$(basename $(IMAGE))-chassis_info.tar docker/

docker-build-extra:
	rm -f docker/*-ramdisk.gz docker/*-bzImage docker/*-chassis_info.tar
