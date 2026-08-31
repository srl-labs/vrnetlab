FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

ARG VERSION
ENV VERSION=${VERSION}
ARG IMAGE

RUN mkdir -p /disk
COPY $IMAGE* /disk/flash.qcow2
COPY *.py /

RUN chmod +x /launch.py /healthcheck.py

EXPOSE 22 443 5000 8443 10000-10099
