FROM public.ecr.aws/docker/library/debian:trixie-slim AS builder

ARG DISK_SIZE=4G

RUN apt-get update -qy && \
   apt-get install -y --no-install-recommends qemu-utils && \
   rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
RUN qemu-img resize /$IMAGE $DISK_SIZE

FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

ARG DISK_SIZE=4G

ARG IMAGE
COPY --from=builder $IMAGE* /
COPY *.py /

EXPOSE 22 5000 10000-10099
