# Talos Linux VM

The [download.sh](download.sh) script will download Talos Linux with serial console enabled. The version is set in the script and can be changed manually.


The following command can be executed to download and build the Talos Linux VM container:

```bash
make build
```

The resulting container will be tagged as `vrnetlab/siderolabs_talos:<version>`.

## Host requirements

* 4 vCPU, 4 GB RAM

## Configuration

No initial configuration is provided (yet)
