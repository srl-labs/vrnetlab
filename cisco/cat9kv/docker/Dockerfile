FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

# Pick up Debian fixes released after vrnetlab-base was built.
RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && rm -rf /var/lib/apt/lists/*

ARG VERSION
ENV VERSION=${VERSION}
ARG IMAGE
COPY $IMAGE* /
COPY *.py /
# for vSwitch.xml file (specifies ASIC emulation parameters), won't throw error if vswitch.xml isn't present
COPY vswitch.xm[l] /img_dir/conf/

EXPOSE 22 161/udp 830 5000 10000-10099
