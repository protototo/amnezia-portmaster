# src/system/scanner.py

import asyncio
import logging
import subprocess
from typing import Set

class HostPortScanner:
    """
    Async-compatible scanner for listening ports on the host machine.
    Uses 'ss' command, assuming the container runs in network_mode: host.
    """

    def _parse_ss_output(self, output: str) -> Set[int]:
        """Parses the output of the 'ss' command to extract port numbers."""
        ports = set()
        lines = output.strip().split('\n')[1:]  # Skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 5:
                try:
                    address_port = parts[4]
                    port_str = address_port.split(':')[-1]
                    if port_str.isdigit():
                        ports.add(int(port_str))
                except (ValueError, IndexError):
                    continue
        return ports

    async def get_listening_ports(self) -> Set[int]:
        """
        Asynchronously gets a set of all listening TCP and UDP ports on the host.
        """
        listening_ports = set()
        try:
            process = await asyncio.to_thread(
                subprocess.run,
                ["ss", "-ltun"], # Listen, TCP, UDP, Numeric
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            listening_ports = self._parse_ss_output(process.stdout)
            logging.info(f"Found {len(listening_ports)} listening ports on the host.")

        except FileNotFoundError:
            logging.error("The 'ss' command was not found. Ensure 'iproute2' is installed in the container.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error executing 'ss': {e.stderr}")
        except Exception as e:
            logging.error(f"An unexpected error occurred while scanning ports: {e}")

        return listening_ports