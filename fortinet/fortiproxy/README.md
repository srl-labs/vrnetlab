# Fortinet FortiProxy

FortiProxy support uses the shared FortiOS launcher in `fortinet/common/fos`.
Place a FortiProxy `qcow2` image in this directory, preferably named
`fortiproxy-vX.Y.Z.qcow2`, then run:

```text
make docker-build-fortiproxy
```

The launcher rejects images that report the wrong product family. Set
`FOS_SKIP_PRODUCT_CHECK=true` only when that validation is intentionally not
possible. `FOS_PRODUCT_VERSION` accepts release prefixes (`8`, `8.0`,
`8.0.1`), exact build selectors (`8.b278`, `8.0.b278`, `8.0.1.b278`), or an inclusive range such as
`7.2-8.0.1`. An unset or empty value leaves version selection in automatic
mode. Prefix selectors validate the version line selected for the image; they
do not query a release catalog to choose the newest image. Build selectors use
the `M.m.p.b#` form and accept four-digit zero-padded builds, such as
`7.2.6.b0465`; four numeric components such as `7.2.6.465` are invalid.

The common `FOS_*` environment variables documented by the FortiGate image
are supported here as well.
