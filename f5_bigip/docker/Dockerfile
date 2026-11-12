FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

# Pick up Debian fixes released after vrnetlab-base was built.
RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && rm -rf /var/lib/apt/lists/*

ARG VERSION
ENV VERSION=${VERSION}
ARG IMAGE

RUN mkdir -p /disk
COPY $IMAGE* /disk/flash.qcow2
COPY *.py /

RUN chmod +x /launch.py /healthcheck.py

EXPOSE 22 443 5000 8443 10000-10099
