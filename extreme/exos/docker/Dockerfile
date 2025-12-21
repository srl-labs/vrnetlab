FROM public.ecr.aws/docker/library/debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends \
   python3 \
   ca-certificates \
   bridge-utils \
   iproute2 \
   socat \
   qemu-kvm \
   qemu-utils \
   tcpdump \
   tftpd-hpa \
   ssh \
   inetutils-ping \
   dnsutils \
   iptables \
   nftables \
   telnet \
   git \
   dosfstools \
   genisoimage \
   ovmf \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 80 161/udp 443 830
HEALTHCHECK CMD ["/healthcheck.py"]
ENTRYPOINT ["/launch.py"]

