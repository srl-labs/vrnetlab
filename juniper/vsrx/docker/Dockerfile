FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
LABEL org.opencontainers.image.authors="roman@dodin.dev,vista@birb.network"

ARG IMAGE
COPY $IMAGE* /

COPY init.conf /
COPY make-config-iso.sh /
COPY *.py /

EXPOSE 22 161/udp 830 5000 10000-10099
