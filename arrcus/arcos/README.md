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
in the ArcOS VM and are mapped to the container interfaces with the same
`swp1`, `swp2`, ... names, as provisioned by the containerlab `arrcus_arcos`
kind.

Note that unlike the ArcOS container image, the data plane interfaces of the
ArcOS VM start at `swp1`, there is no `swp0`.

## Startup configuration

The startup configuration is applied line by line in the ArcOS CLI
configuration mode and committed at the end. Note that the commit is atomic
-- a single invalid line (e.g. a reference to an interface that does not
exist on the VM) rejects the complete startup configuration; the launcher
logs an error in this case.

Since the lines are fed to the CLI directly, close configuration blocks
explicitly with `exit` -- unlike in loaded configuration files, `!` lines
are comments in the CLI and do **not** end a block.

## Usage with containerlab

```yaml
topology:
  nodes:
    arcos1:
      kind: arrcus_arcos
      image: vrnetlab/arrcus_arcos:8.2.1-P5
      startup-config: arcos1.cfg
  links:
    - endpoints: ["arcos1:swp1", "arcos2:swp1"]
```

## Tested versions

* `arcos-8.2.1-P5-VM.kvm.qcow2`
