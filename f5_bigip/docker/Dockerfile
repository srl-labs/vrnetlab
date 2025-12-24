FROM ghcr.io/srl-labs/vrnetlab-base:0.2.1

ARG VERSION
ENV VERSION=${VERSION}
ARG IMAGE

RUN mkdir -p /disk
COPY $IMAGE* /disk/flash.qcow2
COPY *.py /

EXPOSE 22 443 5000 10000-10099
HEALTHCHECK CMD ["/healthcheck.py"]
ENTRYPOINT ["/launch.py"]
