FROM public.ecr.aws/docker/library/debian:bookworm-slim AS builder

ARG DISK_SIZE=4G

RUN apt-get update -qy && \
   apt-get install -y --no-install-recommends qemu-utils && \
   rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
RUN qemu-img resize /$IMAGE $DISK_SIZE

FROM public.ecr.aws/docker/library/debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG DISK_SIZE=4G

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends\
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
   python3-yaml \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY --from=builder $IMAGE* /
COPY *.py /
COPY --chmod=0755 backup.sh /

EXPOSE 22 5000 10000-10099
HEALTHCHECK CMD ["/healthcheck.py"]
ENTRYPOINT ["/launch.py"]
