FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
LABEL maintainer="Kristian Larsson <kristian@spritelink.net>"
LABEL maintainer="Roman Dodin <dodin.roman@gmail.com>"
LABEL maintainer="Stefano Sasso <stesasso@gmail.com>"

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 80 161/udp 443 830 5000 6030 10000-10099 57400
