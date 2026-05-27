# ------------------------------------------------------------------------------
# Nvidia Cumulus VX - vrnetlab / srl-labs Dockerfile
# ------------------------------------------------------------------------------
FROM ghcr.io/srl-labs/vrnetlab-base:0.2.1

ARG VERSION
ARG IMAGE

ENV VERSION=${VERSION}

COPY ${IMAGE} /
COPY *.py     /

EXPOSE 22 8080 8765 5000 10000-10099
