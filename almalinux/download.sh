#!/bin/bash

version="10"
download_url="https://repo.almalinux.org/almalinux/${version}/cloud/x86_64/images/AlmaLinux-${version}-GenericCloud-latest.x86_64.qcow2"
filename="almalinux-${version}-GenericCloud-latest.qcow2"

if [ -e "$filename" ]; then
    echo "File $filename already exists. Skipping download."
else
    # Download the URL
    curl -o $filename "$download_url"
    echo "Download complete: $filename"
fi
