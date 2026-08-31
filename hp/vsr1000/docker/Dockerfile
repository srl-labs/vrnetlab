FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
LABEL maintainer="Kristian Larsson <kristian@spritelink.net>"

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 161/udp 830 5000 6000 10000-10099
