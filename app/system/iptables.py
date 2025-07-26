# src/system/iptables.py

import asyncio
import logging
import re
import subprocess
from typing import Dict, List, Set


class IPTablesError(Exception):
    """Custom exception for errors during iptables command execution."""
    pass


class IPTablesManager:
    """
    An async-compatible class that encapsulates all interactions with iptables.
    It uses asyncio.to_thread to run blocking subprocess calls without blocking the event loop.
    This is an application of the Adapter pattern, adapting the synchronous subprocess API
    to the asynchronous context of our application.
    """

    async def _run_command(self, command: List[str]) -> str:
        """Private helper to execute shell commands asynchronously."""
        try:
            process = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            logging.info(f"Command executed successfully: {' '.join(command)}")
            return process.stdout
        except subprocess.CalledProcessError as e:
            error_message = f"Error executing '{" ".join(command)}'. stderr: {e.stderr.strip()}"
            logging.error(error_message)
            raise IPTablesError(error_message) from e

    async def add_port_forward(self, client_ip: str, port: int):
        """Adds DNAT and FORWARD rules for a given port (TCP/UDP)."""
        for proto in ["tcp", "udp"]:
            # Rule to change destination address (DNAT)
            await self._run_command([
                "iptables", "-t", "nat", "-A", "PREROUTING",
                "-p", proto, "--dport", str(port),
                "-j", "DNAT", "--to-destination", f"{client_ip}:{port}",
            ])
            # Rule to allow the packet to be forwarded
            await self._run_command([
                "iptables", "-A", "FORWARD",
                "-p", proto, "-d", client_ip, "--dport", str(port),
                "-j", "ACCEPT",
            ])
        logging.info(f"Port {port} (TCP/UDP) forwarded to {client_ip}")

    async def remove_port_forward(self, client_ip: str, port: int):
        """Removes the corresponding DNAT and FORWARD rules."""
        for proto in ["tcp", "udp"]:
            await self._run_command([
                "iptables", "-t", "nat", "-D", "PREROUTING",
                "-p", proto, "--dport", str(port),
                "-j", "DNAT", "--to-destination", f"{client_ip}:{port}",
            ])
            await self._run_command([
                "iptables", "-D", "FORWARD",
                "-p", proto, "-d", client_ip, "--dport", str(port),
                "-j", "ACCEPT",
            ])
        logging.info(f"Port forwarding for {port} (TCP/UDP) to {client_ip} removed")

    async def parse_existing_rules(self) -> Dict[str, Set[int]]:
        """Parses existing PREROUTING rules to find current forwards."""
        forwarded_ports: Dict[str, Set[int]] = {}
        try:
            output = await self._run_command(["iptables", "-t", "nat", "-L", "PREROUTING", "-n", "-v"])
            dnat_regex = re.compile(r"dpt:(\d+)\s+to:([\d\.]+):(\d+)")

            for line in output.splitlines():
                match = dnat_regex.search(line)
                if match:
                    port, client_ip, dest_port = match.groups()
                    if port == dest_port:
                        port_num = int(port)
                        forwarded_ports.setdefault(client_ip, set()).add(port_num)

            if forwarded_ports:
                logging.info(f"Found existing forwarded rules: {forwarded_ports}")
            else:
                logging.info("No existing port forwarding rules found.")

        except IPTablesError as e:
            logging.error(f"Failed to parse existing iptables rules: {e}")

        return forwarded_ports