#!/bin/bash

# Arg — client's IP
CLIENT_IP=$1

# Send message to portmaster
echo "DISCONNECT: $CLIENT_IP" | nc -w 1 $PORTMASTER_IP $PORTMASTER_PORT
