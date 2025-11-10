VENDOR=Cisco
NAME=vIOS
IMAGE_FORMAT=qcow2
IMAGE_GLOB=*.qcow2

# Match images like:
# - cisco_vios-158-3.M2.qcow2
# - cisco_viosl2-15.2.qcow2
# - vios_l2-adventerprisek9-m.ssa.high_iron_20200929.qcow2
# Extract version, for example: 158-3.M2 or L2-15.2 or L2-20200929
VERSION=$(shell echo $(IMAGE) | sed \
	-e 's/cisco_vios-\(.*\)\.qcow2/\1/' \
	-e 's/cisco_viosl2-\(.*\)\.qcow2/L2-\1/' \
	-e 's/vios_l2-.*_\([0-9]\{8\}\)\.qcow2/L2-\1/')

-include ../../makefile-sanity.include
-include ../../makefile.include
