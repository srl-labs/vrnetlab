FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
LABEL org.opencontainers.image.authors="roman@dodin.dev"

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends \
   qemu-system-x86 \
   qemu-efi-aarch64 \
   ftp \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 161/udp 830 5000 5678 8291 10000-10099
