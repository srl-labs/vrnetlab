# ------------------------------------------------------------------------------
# Juniper Apstra - vrnetlab / srl-labs Dockerfile
# ------------------------------------------------------------------------------
FROM ghcr.io/srl-labs/vrnetlab-base:0.2.1

ARG VERSION
ARG IMAGE

ENV VERSION=${VERSION}

COPY ${IMAGE} /
COPY *.py     /

EXPOSE 22 80 443 5000 10000-10099