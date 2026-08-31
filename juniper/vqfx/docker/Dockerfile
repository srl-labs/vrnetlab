FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends \
   procps \
   && rm -rf /var/lib/apt/lists/*

ARG RE_IMAGE
ARG PFE_IMAGE

COPY $RE_IMAGE /
COPY $PFE_IMAGE /

COPY healthcheck.py /
COPY vrnetlab.py /
COPY launch.py /

EXPOSE 22 161/udp 830 5000 10000-10099
