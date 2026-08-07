# vrnetlab / Arrcus ArcOS (arcos)

This is the vrnetlab docker image for Arrcus ArcOS.

## Building the docker image

Obtain the ArcOS VM qcow2 image from Arrcus. Place the qcow2 image into this
folder, then run:

```bash
make
```

The image will be tagged based on the version in the filename, e.g.
`arcos-8.2.1-P5-VM.kvm.qcow2` results in `vrnetlab/arrcus_arcos:8.2.1-P5`.

## System requirements

* CPU: 4 cores
* RAM: 16GB
* Disk: <20GB

## Interface mapping

The management interface appears as `ma1` in the ArcOS VM and is mapped to
`eth0` of the container. Data plane interfaces appear as `swp1`, `swp2`, ...
in the ArcOS VM and are mapped to `eth1`, `eth2`, ... of the container.

## Tested versions

* `arcos-8.2.1-P5-VM.kvm.qcow2`
