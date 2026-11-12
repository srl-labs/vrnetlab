FROM public.ecr.aws/docker/library/debian:trixie-slim AS builder

ARG DISK_SIZE=4G

RUN apt-get update -qy && \
   apt-get install -y --no-install-recommends qemu-utils && \
   rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE* /
RUN qemu-img resize /$IMAGE $DISK_SIZE

FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

# Pick up Debian fixes released after vrnetlab-base was built.
RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && rm -rf /var/lib/apt/lists/*

ARG DISK_SIZE=4G

ARG IMAGE
COPY --from=builder $IMAGE* /
COPY *.py /

EXPOSE 22 5000 10000-10099
