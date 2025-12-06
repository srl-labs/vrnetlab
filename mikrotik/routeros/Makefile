VENDOR=Mikrotik
NAME=RouterOS
IMAGE_FORMAT=vmdk/vdi
IMAGE_GLOB=*.vmdk *.vdi

# match versions like:
# chr-6.39.2.vmdk -> 6.39.2
# chr-7.20.4-arm64.vdi -> 7.20.4-arm64
VERSION=$(shell echo $(IMAGE) | sed -rn 's/.*chr-(.+)\.(vmdk|vdi)/\1/p')

-include ../../makefile-sanity.include
-include ../../makefile.include
