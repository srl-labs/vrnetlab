# base image dockerfile is defined in https://github.com/srl-labs/vrnetlab
FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

ARG DEBIAN_FRONTEND=noninteractive
# sshpass is used by backup.sh; the base image doesn't ship it.
RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends sshpass \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
COPY *.py /
COPY backup.sh /

EXPOSE 22 443 5000 8080
