# src/core/config.py

import logging
import os
import sys
from dataclasses import dataclass

# Setup logging to output to stdout, as is standard for Docker.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)


@dataclass
class Config:
    """
    Centralized and validated configuration from environment variables.
    This now only contains settings core to the daemon's operation,
    including the master admin key.
    """
    vpn_ip: str
    daemon_port: int
    exposed_ports: range
    admin_api_key: str  # The one key to rule them all

    @classmethod
    def from_env(cls) -> "Config":
        """Factory method to create a configuration from environment variables."""
        vpn_ip = os.environ.get("PORTMASTER_IP")
        if not vpn_ip:
            logging.critical(
                "Critical Error: PORTMASTER_IP environment variable is not set! Daemon cannot start."
            )
            sys.exit(1)

        admin_api_key = os.environ.get("PORTMASTER_ADMIN_API_KEY")
        if not admin_api_key:
            logging.critical(
                "Critical Error: PORTMASTER_ADMIN_API_KEY environment variable is not set! The API is insecure."
            )
            sys.exit(1)

        try:
            daemon_port = int(os.environ.get("PORTMASTER_PORT", "5000"))
        except (ValueError, TypeError):
            logging.warning("PORTMASTER_PORT is invalid or not set. Using default port 5000.")
            daemon_port = 5000

        port_range_str = os.environ.get("EXPOSED_PORT_RANGE", "20000-25000")
        try:
            start_str, end_str = port_range_str.split("-")
            start, end = int(start_str), int(end_str)
            if start >= end:
                raise ValueError("Range start must be less than range end.")
            exposed_ports = range(start, end + 1)
        except ValueError as e:
            logging.error(f"Error in EXPOSED_PORT_RANGE ('{port_range_str}'): {e}. Using default range.")
            exposed_ports = range(20000, 25001)

        logging.info(
            f"Configuration loaded: Listening on IP={vpn_ip}, Port={daemon_port}, "
            f"Range={exposed_ports.start}-{exposed_ports.stop - 1}"
        )
        return cls(vpn_ip, daemon_port, exposed_ports, admin_api_key)

# Create a single, globally accessible config instance.
settings = Config.from_env()