FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG DISK_SIZE=4G

RUN apt-get update -qy \
   && apt-get install -y \
   bridge-utils \
   iproute2 \
   socat \
   qemu-kvm \
   tcpdump \
   ssh \
   inetutils-ping \
   dnsutils \
   iptables \
   nftables \
   telnet \
   cloud-utils \
   sshpass \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
COPY *.py /
COPY backup.sh /
COPY copy_etc_and_run_startupscript.sh /

RUN qemu-img resize /${IMAGE} ${DISK_SIZE}

EXPOSE 22 5000 10000-10099
HEALTHCHECK CMD ["/healthcheck.py"]
ENTRYPOINT ["/launch.py"]
