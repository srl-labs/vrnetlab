# base image dockerfile is defined in https://github.com/hellt/vrnetlab
FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

ARG IMAGE
# launch.py detects VCE/VCC/VCO from the original image filename
ENV VC_IMAGE=${IMAGE}

# VCE/VCC: the single qcow2; VCO: rootfs + store disks extracted from the tar
COPY *.qcow2 /
COPY *.py /

EXPOSE 22 443 5000 10000-10099
