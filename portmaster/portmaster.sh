#!/usr/bin/env python3

import socket
import subprocess
import logging
import re
import os
import sys
import signal

VPN_IP = os.environ.get('PORTMASTER_IP')
DAEMON_PORT = 5000
EXPOSED_PORT_RANGE = range(20601, 20811)  # Default forwarded port range
forwarded_ports = {}  # Dictionary to store forwarded ports
running = True  # Variable to control daemon operation
LOG_PATH = '/opt/portmaster/log/portmaster.log'

# Logging configuration
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format='%(asctime)s %(message)s')

# Get the value of the EXPOSED_PORT_RANGE environment variable
port_range = os.environ.get("EXPOSED_PORT_RANGE")

if port_range:
    try:
        # Split the string into the start and end of the range
        start, end = map(int, port_range.split('-'))
        EXPOSED_PORT_RANGE = range(start, end + 1)  # Include the last port in the range
        logging.info(f"Used port range: {EXPOSED_PORT_RANGE}")
    except ValueError:
        logging.info(f"Error: Incorrect format of the EXPOSED_PORT_RANGE environment variable: {port_range}")

else:
    logging.info("Error: The EXPOSED_PORT_RANGE environment variable is not set. Using default values!")

# Get the environment variable value
daemon_port_str = os.environ.get('PORTMASTER_PORT')

# Check that the value is not None and convert it to an integer
if daemon_port_str is not None:
    try:
        DAEMON_PORT = int(daemon_port_str)
    except ValueError:
        logging.info(f"Error: Could not convert value '{daemon_port_str}' to an integer.")
else:
    logging.info("Error: The 'PORTMASTER_PORT' environment variable is not set. Using the default value of 5000")


def signal_handler(signum, frame):
    global running
    logging.info(f'Signal {signum} received, stopping the daemon...')
    running = False


def parse_iptables():
    """Parses iptables output to fill the forwarded ports dictionary."""
    global forwarded_ports
    try:
        # Get iptables output
        command = ['sudo', 'iptables', '-t', 'nat', '-L', '-n', '--line-numbers']
        output = subprocess.check_output(command, universal_newlines=True)
        logging.info(f'Parsing iptables output:\n{output}')

        # Regular expressions to search for rules
        for line in output.splitlines():
            if "DNAT" in line:
                match = re.search(
                    r'DNAT\s+(tcp|udp)\s+--\s+0.0.0.0/0\s+0.0.0.0/0\s+(tcp|udp)\s+dpt:(\d+)\s+to:(\d+\.\d+\.\d+\.\d+):(\d+)',
                    line)
                if match:
                    protocol = match.group(1)
                    port = int(match.group(3))
                    client_ip = match.group(4)

                    if client_ip not in forwarded_ports:
                        forwarded_ports[client_ip] = {'tcp': [], 'udp': []}
                    forwarded_ports[client_ip][protocol].append(port)
                    logging.info(f'Found existing port forwarding: {protocol.upper()} Port {port} to {client_ip}')
    except Exception as e:
        logging.error(f'Error parsing iptables: {e}')


def add_port_forwarding(client_ip, ports):
    try:
        # Define protocols for forwarding
        protocols = ['tcp', 'udp']

        for protocol in protocols:
            # Add rule for PREROUTING
            command_prerouting = [
                'sudo', 'iptables', '-t', 'nat', '-A', 'PREROUTING', '-p', protocol,
                '--dport', str(ports[0]), '-j', 'DNAT', '--to-destination', f'{client_ip}:{ports[0]}'
            ]
            subprocess.run(command_prerouting, check=True)
            logging.info(f'{protocol.upper()} Port {ports[0]} forwarded to {client_ip} (PREROUTING)')

            # Add rule for FORWARD
            command_forward = [
                'sudo', 'iptables', '-A', 'FORWARD', '-p', protocol, '-d', client_ip,
                '--dport', str(ports[0]), '-j', 'ACCEPT'
            ]
            subprocess.run(command_forward, check=True)
            logging.info(f'{protocol.upper()} Port {ports[0]} allowed in FORWARD')

         # Add rule for POSTROUTING (Optional - often handled by default routing)
            command_postrouting = [
                 'sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-p', protocol,
                 '-d', client_ip, '--dport', str(ports[0]), '-j', 'MASQUERADE'
             ]
#             subprocess.run(command_postrouting, check=True)
#             logging.info(f'{protocol.upper()} Port {ports[0]} masqueraded in POSTROUTING')



        return True
    except subprocess.CalledProcessError as e:
        logging.error(f'Failed to add port forwarding for {client_ip}:{ports} - {e}')
        return False


def handle_client_connection(client_socket, client_ip):
    try:
        # Get the request from the client
        request = client_socket.recv(1024).decode('utf-8').strip()

        if request.startswith("PORTS:"):
            ports_str = request.replace("PORTS:", "").strip()  # Remove leading/trailing whitespace
            if not ports_str: # Handle empty or whitespace-only input explicitly
                ports = [] # Set ports to an empty list, triggering port removal only
            else:
                ports = [int(port) for port in ports_str.split(",") if port.strip().isdigit()]

            removed_ports = []
            if client_ip in forwarded_ports:  # Remove existing ports before adding new ones
                for protocol in ['tcp', 'udp']:
                    for port in forwarded_ports[client_ip][protocol]:
                        remove_port_forwarding(client_ip, port)
                        removed_ports.append(port)
                del forwarded_ports[client_ip]

            success_ports = []
            failed_ports = []

            if ports: #Proceed with forwarding the requested ports
                for port in ports:
                    if port not in EXPOSED_PORT_RANGE:
                        logging.error(f"Port {port} is not in the allowed range: {EXPOSED_PORT_RANGE}")
                        failed_ports.append(port)
                        continue
                    # Check if the port is already forwarded for ANY client, not just this one.
                    if any(port in forwarded_ports.get(ip, {}).get(protocol, []) for ip in forwarded_ports for protocol in ['tcp', 'udp']):
                        logging.error(f"Port {port} is already forwarded to another IP.")
                        failed_ports.append(port)
                        continue  # Skip to the next port
                    if add_port_forwarding(client_ip, [port]):
                        success_ports.append(port)
                    else:
                        failed_ports.append(port)

            # Send responses about removed, successful, and failed ports
            if removed_ports:
                client_socket.send(f"Removed ports: {', '.join(map(str, removed_ports))}\n".encode('utf-8'))
            if success_ports:
                response = f"Success: Ports {', '.join(map(str, success_ports))} forwarded\n"
                client_socket.send(response.encode('utf-8'))
            if failed_ports:
                response = f"Error: Failed to forward ports {', '.join(map(str, failed_ports))}\n"
                client_socket.send(response.encode('utf-8'))
                # Send the client information about which ports were removed
                if removed_ports:
                    client_socket.send(f"Removed ports: {', '.join(map(str, removed_ports))}\n".encode('utf-8'))

                # Send the result of forwarding new ports, if any
                if success_ports:
                    response = f"Success: Ports {', '.join(map(str, success_ports))} forwarded\n"
                    client_socket.send(response.encode('utf-8'))
                if failed_ports:
                    response = f"Error: Failed to forward ports {', '.join(map(str, failed_ports))}\n"
                    client_socket.send(response.encode('utf-8'))

        elif request.startswith("DISCONNECT:"):
           
            # Handle disconnection – ignore the IP in the request if not from VPN_IP
            requested_ip = request.replace("DISCONNECT:", "").strip()            
            disconnect_ip = client_ip if client_ip != VPN_IP else requested_ip #Use client's IP unless request is coming from the VPN server itself.
            logging.info(f"Got request: {request} | from IP: {client_ip}")
            logging.info(f'Disconnecting ports for {disconnect_ip} (requested IP: {requested_ip if requested_ip else "None"})')

            if not disconnect_ip:  # Still check for empty disconnect_ip
                response = "Error: Invalid disconnect request. No IP address provided.\n"
                client_socket.send(response.encode('utf-8'))
                return

            # Check for an empty string or incorrect format
            if not disconnect_ip:
                response = "Error: Invalid disconnect request. No IP address provided.\n"
                client_socket.send(response.encode('utf-8'))
                return

            if disconnect_ip in forwarded_ports:
                for protocol in ['tcp', 'udp']:
                    for port in forwarded_ports[disconnect_ip][protocol]:
                        remove_port_forwarding(disconnect_ip, port)
                del forwarded_ports[disconnect_ip]  # Remove the record
                response = f"Disconnected: Ports for {disconnect_ip} removed\n"
            else:
                response = f"Error: No ports are forwarded to {disconnect_ip}\n"
            client_socket.send(response.encode('utf-8'))
        else:
            response = "Error: Unknown command\n"
            client_socket.send(response.encode('utf-8'))

    except Exception as e:
        client_socket.send(f"Error: {str(e)}\n".encode('utf-8'))
    finally:
         client_socket.close()


def remove_port_forwarding(client_ip, port):
    try:
        protocols = ['tcp', 'udp']
        for protocol in protocols:
            # Remove rule from PREROUTING
            command_prerouting = [
                'sudo', 'iptables', '-t', 'nat', '-D', 'PREROUTING', '-p', protocol,
                '--dport', str(port), '-j', 'DNAT', '--to-destination', f'{client_ip}:{port}'
            ]
            subprocess.run(command_prerouting, check=True)
            logging.info(f'Removed {protocol.upper()} Port {port} from {client_ip} (PREROUTING)')

            # Remove rule from FORWARD
            command_forward = [
                'sudo', 'iptables', '-D', 'FORWARD', '-p', protocol, '-d', client_ip,
                '--dport', str(port), '-j', 'ACCEPT'
            ]
            subprocess.run(command_forward, check=True)
            logging.info(f'Removed {protocol.upper()} Port {port} from FORWARD')

            # Remove rule from POSTROUTING (If it was added)
            command_postrouting = [
                'sudo', 'iptables', '-t', 'nat', '-D', 'POSTROUTING', '-p', protocol,
                '-d', client_ip, '--dport', str(port), '-j', 'MASQUERADE'
            ]
#             subprocess.run(command_postrouting, check=True)
#             logging.info(f'Removed {protocol.upper()} Port {port} from POSTROUTING')


    except subprocess.CalledProcessError as e:
        logging.error(f'Failed to remove port forwarding for {client_ip}:{port} - {e}')


# Function to create a background process
def daemonize():
    # First fork() to create a child process
    if os.fork() > 0:
        sys.exit()  # The parent process exits

    # Create a new session
    os.setsid()

    # Second fork() to detach from the terminal
    if os.fork() > 0:
        sys.exit()  # The first child process exits

    # Now we are in the child process that is not bound to the terminal

    # Redirect standard streams to devnull
    devnull = open(os.devnull, 'r+')
    for fd in [sys.stdin, sys.stdout, sys.stderr]:
        os.dup2(devnull.fileno(), fd.fileno())



def run_daemon():
    """Run daemon"""
    parse_iptables()  # Fill the dictionary on startup

    # Bind the SIGTERM signal handler
    signal.signal(signal.SIGTERM, signal_handler)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind((VPN_IP, DAEMON_PORT))
        server_socket.listen(5)
        logging.info(f'Daemon started on {VPN_IP}:{DAEMON_PORT}')

        while running:  # Check the running variable
            try:
                client_socket, client_address = server_socket.accept()
                client_ip = client_address[0]
                logging.info(f'Accepted connection from {client_ip}')

                handle_client_connection(client_socket, client_ip)
            except OSError: # Handle potential errors during accept() if the socket is closed
                if not running:  # If the daemon was stopped
                    break

    logging.info('Daemon stopped gracefully.')


def main():
    # Check if the daemon is running as root
    if os.geteuid() == 0:
        print("Error: Daemon should not be run as root.")
        sys.exit(1)
    daemonize()
    run_daemon()


if __name__ == '__main__':
    main()