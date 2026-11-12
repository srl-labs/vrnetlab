# Stage 1: unpack the Simulate ONTAP OVA and convert its four VMDK disks
# (boot/CF card, NVRAM, /var, simulated disk shelf) to qcow2.
FROM public.ecr.aws/docker/library/debian:trixie-slim AS builder

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends qemu-utils \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY $IMAGE /ova/
RUN cd /ova \
   && tar -xf "$IMAGE" \
   && for i in 1 2 3 4; do \
        qemu-img convert -O qcow2 "$(ls *-disk$i.vmdk)" /disk$i.qcow2; \
      done \
   && rm -rf /ova

FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

# Pick up Debian fixes released after vrnetlab-base was built.
RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && rm -rf /var/lib/apt/lists/*

ARG VERSION
ENV VERSION=${VERSION}

COPY --from=builder /disk1.qcow2 /disk2.qcow2 /disk3.qcow2 /disk4.qcow2 /
COPY *.py /

EXPOSE 22 80 443 5000 10000-10099
