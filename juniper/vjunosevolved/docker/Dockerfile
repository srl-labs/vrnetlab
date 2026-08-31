FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
LABEL org.opencontainers.image.authors="roman@dodin.dev,vista@birb.network"

ARG IMAGE
COPY $IMAGE* /

# copy conf file
COPY init.conf /
# copy config shell script
COPY make-config.sh /
# copy python scripts for launching VM
COPY *.py /

EXPOSE 22 161/udp 830 5000 10000-10099 57400
