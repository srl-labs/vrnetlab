#!/usr/bin/env bash
# Smoke test for the vrnetlab Arrcus ArcOS image.
#
# Boots the image the way containerlab would, waits for the launcher to
# finish bootstrapping, then verifies over ssh that the bootstrap and
# startup configuration were applied.
#
# Usage: ./smoke-test.sh [image]
#   image defaults to the most recently built vrnetlab/arrcus_arcos tag
#
# Requires: docker, /dev/kvm

set -euo pipefail

IMAGE="${1:-$(docker images --format '{{.Repository}}:{{.Tag}}' vrnetlab/arrcus_arcos | head -1)}"
NAME="arcos-smoketest-$$"
USERNAME="vrnetlab"
PASSWORD="VR-netlab9"
NODE_HOSTNAME="arcos-smoke"
STARTUP_TIMEOUT=900

if [ -z "$IMAGE" ]; then
    echo "ERROR: no vrnetlab/arrcus_arcos image found, run 'make' first" >&2
    exit 1
fi

CFG_DIR=$(mktemp -d)

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    rm -rf "$CFG_DIR"
}
trap cleanup EXIT

cat > "$CFG_DIR/startup-config.cfg" <<'EOF'
system login-banner "vrnetlab smoke test"
system domain-name smoke.test
EOF

echo "==> starting $IMAGE as $NAME"
docker run -d --name "$NAME" --privileged \
    -v "$CFG_DIR":/config \
    "$IMAGE" \
    --username "$USERNAME" --password "$PASSWORD" \
    --hostname "$NODE_HOSTNAME" --connection-mode tc >/dev/null

echo "==> waiting for 'Startup complete' (up to ${STARTUP_TIMEOUT}s)"
deadline=$((SECONDS + STARTUP_TIMEOUT))
while ! docker logs "$NAME" 2>&1 | grep -q "Startup complete"; do
    if docker logs "$NAME" 2>&1 | grep -qE "Traceback|QemuBroken"; then
        echo "FAIL: launcher error" >&2
        docker logs "$NAME" 2>&1 | tail -30 >&2
        exit 1
    fi
    if [ "$SECONDS" -gt "$deadline" ]; then
        echo "FAIL: timed out waiting for startup" >&2
        docker logs "$NAME" 2>&1 | tail -30 >&2
        exit 1
    fi
    sleep 10
done
echo "    OK ($(docker logs "$NAME" 2>&1 | grep -o 'Startup complete in: [0-9:.]*'))"

echo "==> waiting for container health check"
status="starting"
for _ in $(seq 1 30); do
    status=$(docker inspect "$NAME" --format '{{.State.Health.Status}}')
    [ "$status" = "healthy" ] && break
    sleep 5
done
if [ "$status" != "healthy" ]; then
    echo "FAIL: container health is '$status'" >&2
    exit 1
fi
echo "    OK"

echo "==> verifying ssh access and applied configuration"
config=$(docker exec "$NAME" bash -c \
    "sshpass -p $PASSWORD ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
     $USERNAME@localhost 'show running-config system' 2>/dev/null")

rc=0
check() {
    if grep -qF "$1" <<< "$config"; then
        echo "    OK: $1"
    else
        echo "    FAIL: missing '$1'" >&2
        rc=1
    fi
}

check "system hostname $NODE_HOSTNAME"
check "system ssh-server enable true"
check "system aaa authentication user $USERNAME"
check "SYSTEM_ROLE_ADMIN"
# from the startup config
check 'system login-banner "vrnetlab smoke test"'
check "system domain-name smoke.test"

if [ "$rc" -eq 0 ]; then
    echo "SMOKE TEST PASSED"
else
    echo "SMOKE TEST FAILED" >&2
fi
exit "$rc"
