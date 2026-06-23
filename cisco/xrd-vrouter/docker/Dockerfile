FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends cloud-image-utils curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY make-golden.sh guest-init.sh xrd-guest-init.service netplan-mgmt.yaml \
     cloud-init-disable-net.cfg xrd-unconfined launch.py /
RUN chmod +x /make-golden.sh

EXPOSE 22 161/udp 830 9339 57400 10000-10099
