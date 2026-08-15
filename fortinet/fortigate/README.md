# Fortinet Fortigate

Support for the Fortinet Fortigate launched by containerlab.

## Building the docker image

Add your qcow2 image to the root of this folder.
Naming format: fortios-vX.Y.Z.qcow2

`make`

## Running the docker image manually

If you need to run the image without using containerlab:

`make docker-run-fortigate`

## Credentials

The launcher logs in with `--username` / `--password`, defaulting to
`admin` / `admin`.

FortiOS forces a password change on the first login, and from 7.6 it also
ships a password policy enabled by default that requires at least 12
characters including an upper case letter, a lower case letter, a digit and a
non-alphanumeric character. A weak password such as the `admin` default cannot
satisfy that, so the launcher completes the forced change with a compliant
temporary password, disables the password policy, and then applies the
requested password. Disabling the policy is what allows `admin` / `admin` to
keep working on 7.6 and later.

## Tested versions

* Fortigate 7.0.14 KVM
* Fortigate 7.4.12 KVM
* Fortigate 7.6.7 KVM
* Fortigate 8.0.0 KVM

These were all tested without a licence installed, which is enough for the
launcher itself: it bootstraps over the serial console, and CLI access over
console and SSH works on an unlicensed unit.

## Licensing

An unlicensed FortiGate-VM reports `License Status: Invalid`. It boots and
accepts configuration over the console and over SSH, but **the REST API does
not work**. An API request with a valid `api-user` and token is rejected with
HTTP 401, and FortiOS logs the reason as an internal error rather than an
authentication or trusthost failure:

```
logdesc="Admin login failed" user="apitest" ui="https(...)" method="https"
action="login" status="failed" reason="internal_error"
```

This matters for anything driving the device over HTTPS rather than SSH --
netlab, for instance, configures FortiOS with the `fortinet.fortios` Ansible
collection over `httpapi` on port 443. Installing a licence is reported to
resolve it; Fortinet offers a free permanent trial licence that requires a
FortiCare account, though that licence carries its own limits (1 vCPU, 2 GiB
RAM, and a maximum of three interfaces, firewall policies and routes).

FortiGuard-dependent features are unavailable in either case.
