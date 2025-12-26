# base image dockerfile is defined in https://github.com/hellt/vrnetlab
FROM ghcr.io/srl-labs/vrnetlab-base:0.2.0

ARG DEBIAN_FRONTEND=noninteractive

RUN uv add requests

ARG IMAGE
COPY $IMAGE* /
COPY *.py /
