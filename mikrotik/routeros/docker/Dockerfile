FROM public.ecr.aws/docker/library/debian:bookworm-slim
LABEL org.opencontainers.image.authors="roman@dodin.dev"

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qy \
   && apt-get upgrade -qy \
   && apt-get install -y \
   bridge-utils \
   iproute2 \
   python3-ipy \
   socat \
   qemu-kvm \
   qemu-system-x86 \
   tcpdump \
   ssh \
   inetutils-ping \
   dnsutils \
   iptables \
   nftables \
   telnet \
   ftp \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 161/udp 830 5000 5678 8291 10000-10099
HEALTHCHECK CMD ["/healthcheck.py"]
ENTRYPOINT ["/launch.py"]
