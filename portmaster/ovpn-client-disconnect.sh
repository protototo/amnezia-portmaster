#!/bin/bash

# Get client's IP
CLIENT_IP=$ifconfig_pool_remote_ip

# Send message to portmaster
echo "DISCONNECT: $CLIENT_IP" | nc -w 1 $PORTMASTER_IP $PORTMASTER_PORT