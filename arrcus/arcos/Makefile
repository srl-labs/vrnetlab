VENDOR=Arrcus
NAME=ArcOS
IMAGE_FORMAT=qcow2
IMAGE_GLOB=arcos*.qcow2

# match versions like:
# arcos-8.2.1-P5-VM.kvm.qcow2 -> 8.2.1-P5
VERSION=$(shell echo $(IMAGE) | sed -n 's/^arcos-\(.*\)-VM\.kvm\.qcow2$$/\1/p')

-include ../../makefile-sanity.include
-include ../../makefile.include
