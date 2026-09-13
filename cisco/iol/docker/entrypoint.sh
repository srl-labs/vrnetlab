#!/bin/bash

echo "Launching IOL with PID" $IOL_PID

# Clear ip addressing on eth0 (it 'belongs' to IOL now)
ip addr flush dev eth0
ip -6 addr flush dev eth0

echo "Flushed eth0 addresses"

sleep 5

# Run IOUYAP
exec /usr/bin/iouyap 513 -q &

# Use the declared slot count when provided, otherwise get the highest numbered eth interface
if [ -n "$CLAB_IOL_NUM_SLOTS" ]; then
    num_slots=$CLAB_IOL_NUM_SLOTS
else
    max_eth=$(ls /sys/class/net | grep eth | grep -o -E '[0-9]+' | sort -n | tail -1)
    num_slots=$(( (max_eth + 4) / 4 ))
fi

# Start IOL
exec /iol/iol.bin $IOL_PID -e $num_slots -s 0 -c config.txt -n 1024
