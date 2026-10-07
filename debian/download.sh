#!/bin/bash

version_name="bookworm"
version_number="12"

# Download latest bookworm lts cloud image
download_url="https://cloud.debian.org/images/cloud/$version_name/latest/debian-$version_number-genericcloud-amd64.qcow2"

# Extract the filename from the URL
filename="debian-$version_name-genericcloud.qcow2"

# Check if the file already exists in the current directory
if [ -e "$filename" ]; then
    echo "File $filename already exists. Skipping download."
else
    # Download the URL
    curl -o $filename -L "$download_url"
    echo "Download complete: $filename"
fi
