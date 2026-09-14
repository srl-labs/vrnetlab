FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends cloud-image-utils curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# vrnetlab.py is the shared launcher library. The generic build step
# (docker-build-common in makefile.include) stages common/vrnetlab.py into this
# build context, but because this Dockerfile lists files explicitly rather than
# `COPY *.py /`, vrnetlab.py must be named here too -- otherwise it is NOT copied
# in and the image keeps the (older) /vrnetlab.py baked into vrnetlab-base, so
# launch.py's calls into the current common lib (strip_mgmt_interface_config,
# the qemu-guest-agent channel) fail with AttributeError.
COPY make-golden.sh guest-init.sh xrd-guest-init.service netplan-mgmt.yaml \
     cloud-init-disable-net.cfg xrd-unconfined launch.py vrnetlab.py /
RUN chmod +x /make-golden.sh

EXPOSE 22 161/udp 830 9339 57400 10000-10099
