VENDOR=NetApp
NAME=ONTAP
IMAGE_FORMAT=ova
IMAGE_GLOB=vsim-netapp-DOT*.ova

# match versions like:
# vsim-netapp-DOT9.17.1-cm_nodar.ova
# vsim-netapp-DOT9.15.1-cm_nodar.ova
VERSION=$(shell echo $(IMAGE) | sed -e 's/vsim-netapp-DOT\([0-9.A-Za-z]\+\)-cm_.*\.ova/\1/')

-include ../../makefile-sanity.include
-include ../../makefile.include
