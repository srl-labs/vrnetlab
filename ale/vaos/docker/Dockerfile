FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update -qy \
 && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
 && apt-get install -y --no-install-recommends qemu-system-x86 qemu-utils telnet \
 && rm -rf /var/lib/apt/lists/*

ARG IMAGE
# vAOS disk (hda), then kernel, initrd and chassis information disk (hdb)
COPY $IMAGE* /
COPY *-ramdisk.gz *-bzImage *-chassis_info.tar /
COPY *.py /

EXPOSE 22 161/udp 830 5000 10000-10099
