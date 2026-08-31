FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
MAINTAINER Stefano Sasso <stesasso@gmail.com>

RUN apt-get update -qy \
 && apt-get install -y --no-install-recommends \
    ftp \
    qemu-system-x86 \
 && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 80 161/udp 443 830 5000 5678 8291 10000-10099
