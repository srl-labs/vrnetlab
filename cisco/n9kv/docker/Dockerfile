FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && apt-get install -y --no-install-recommends \
   udev vsftpd ftp wget

ARG IMAGE
COPY $IMAGE* /
COPY OVMF.fd /
COPY *.py /
