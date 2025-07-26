# src/services/port_manager.py

import asyncio
import logging
import secrets
from typing import Dict, Set, Tuple, List, Optional

from app.core.config import Config
from app.system.iptables import IPTablesManager, IPTablesError
from app.system.scanner import HostPortScanner
from app.api.models import ClientInfo  # We need this for type hinting


class PortMasterService:
    def __init__(self, config: Config, iptables_manager: IPTablesManager, host_port_scanner: HostPortScanner):
        self.config = config
        self.iptables = iptables_manager
        self.scanner = host_port_scanner
        self._lock = asyncio.Lock()

        # --- STATE ATTRIBUTES ---
        # Stores forwarded ports: { "vpn_client_ip": {port1, port2} }
        self.forwarded_ports: Dict[str, Set[int]] = {}
        # Stores ports from global pool that are occupied by host processes
        self.unavailable_ports: Set[int] = set()
        # --- NEW: In-memory client database ---
        # { "client_id": ClientInfo_object }
        self.clients: Dict[str, ClientInfo] = {}
        # For fast lookup: { "api_key": "client_id" }
        self.api_key_to_client_id: Dict[str, str] = {}

    async def initialize(self):
        logging.info("Initializing PortManagerService...")
        async with self._lock:
            host_ports = await self.scanner.get_listening_ports()
            config_ports = set(self.config.exposed_ports)
            self.unavailable_ports = config_ports.intersection(host_ports)
            self.forwarded_ports = await self.iptables.parse_existing_rules()
        logging.info("PortManagerService initialized successfully.")

    # --- NEW: Client Management Methods (for Admin) ---

    async def create_client(self, client_id: str, port_range_str: str) -> Optional[ClientInfo]:
        async with self._lock:
            if client_id in self.clients:
                logging.warning(f"Admin tried to create client with existing ID: {client_id}")
                return None  # Or raise a specific exception

            try:
                start_str, end_str = port_range_str.split("-")
                start, end = int(start_str), int(end_str)
                if not (start in self.config.exposed_ports and end in self.config.exposed_ports and start <= end):
                    raise ValueError("Provided range is not a valid sub-set of the global exposed range.")

                new_api_key = secrets.token_hex(16)
                client_data = ClientInfo(
                    client_id=client_id,
                    api_key=new_api_key,
                    allowed_ports=list(range(start, end + 1))
                )
                self.clients[client_id] = client_data
                self.api_key_to_client_id[new_api_key] = client_id
                logging.info(f"Admin created new client '{client_id}' with port range {port_range_str}")
                return client_data
            except ValueError as e:
                logging.error(f"Admin provided invalid port range '{port_range_str}' for client '{client_id}': {e}")
                return None

    async def delete_client(self, client_id: str) -> bool:
        async with self._lock:
            if client_id not in self.clients:
                return False

            # Clean up forwarded ports for this client across all their possible IPs
            client_to_remove = self.clients[client_id]
            ports_to_remove = set()
            for ip, rules in list(self.forwarded_ports.items()):
                # This logic assumes we can identify which rules belong to the client.
                # A more robust system would store { client_id: { ip: {ports} } }
                # For now, we assume any user of this key is this client.
                # On deletion, we can't know the IP, so this part is tricky.
                # A better approach: do nothing here, let ports be cleaned up by user disconnect.
                pass

            del self.api_key_to_client_id[client_to_remove.api_key]
            del self.clients[client_id]
            logging.info(f"Admin deleted client '{client_id}'")
            return True

    def get_all_clients(self) -> List[ClientInfo]:
        return list(self.clients.values())

    def get_client_by_key(self, api_key: str) -> Optional[ClientInfo]:
        client_id = self.api_key_to_client_id.get(api_key)
        if client_id:
            return self.clients.get(client_id)
        return None

    # --- MODIFIED: User-facing Methods ---

    async def update_client_ports(
            self, client_ip: str, requested_ports_set: Set[int], allowed_ports: List[int]
    ) -> Tuple[Set[int], Set[int]]:
        async with self._lock:
            allowed_ports_set = set(allowed_ports)
            old_ports_for_client = self.forwarded_ports.get(client_ip, set())
            other_clients_ports = {p for ip, ports in self.forwarded_ports.items() if ip != client_ip for p in ports}

            ports_to_remove = old_ports_for_client - requested_ports_set
            for port in ports_to_remove:
                await self.iptables.remove_port_forward(client_ip, port)

            ports_to_add = requested_ports_set - old_ports_for_client
            successful_adds, failed_adds = set(), set()

            for port in ports_to_add:
                if port not in allowed_ports_set:
                    logging.warning(f"Port {port} is outside the allowed sub-pool for the client at {client_ip}.")
                    failed_adds.add(port)
                elif port in self.unavailable_ports:
                    failed_adds.add(port)
                elif port in other_clients_ports:
                    failed_adds.add(port)
                else:
                    try:
                        await self.iptables.add_port_forward(client_ip, port)
                        successful_adds.add(port)
                    except IPTablesError:
                        failed_adds.add(port)

            current_client_ports = (old_ports_for_client - ports_to_remove).union(successful_adds)
            if current_client_ports:
                self.forwarded_ports[client_ip] = current_client_ports
            elif client_ip in self.forwarded_ports:
                del self.forwarded_ports[client_ip]

            all_failed = failed_adds.union(ports_to_add - successful_adds)
            return successful_adds, all_failed

    async def disconnect_client_ip(self, client_ip: str) -> int:
        # This now just disconnects an IP, not a logical client
        async with self._lock:
            if client_ip not in self.forwarded_ports: return 0

            ports_to_remove = self.forwarded_ports.get(client_ip, set())
            removed_count = 0
            for port in list(ports_to_remove):
                await self.iptables.remove_port_forward(client_ip, port)
                removed_count += 1

            del self.forwarded_ports[client_ip]
            return removed_count