# src/api/models.py

from pydantic import BaseModel, Field
from typing import Dict, List, Optional

# --- Client Management Models (for Admin) ---

class ClientCreateRequest(BaseModel):
    """Request model for creating a new client."""
    client_id: str = Field(..., description="A unique identifier for the client, e.g., 'user-john-doe'.")
    port_range: str = Field(..., description="The sub-pool of ports assigned to this client.", example="21000-21010")

class ClientInfo(BaseModel):
    """Full information about a client, including their secret API key."""
    client_id: str
    api_key: str = Field(..., description="The auto-generated API key for this client. Treat this as a secret!")
    allowed_ports: List[int]

class ClientInfoPublic(BaseModel):
    """Publicly viewable information about a client (excludes API key)."""
    client_id: str
    allowed_ports: List[int]

# --- User-facing Models ---

class PortForwardRequest(BaseModel):
    """Request model for updating port forwarding rules."""
    ports: List[int] = Field(..., description="A list of ports to be forwarded from YOUR assigned pool.")

class PortForwardResponse(BaseModel):
    """Response model after a port forwarding request."""
    message: str
    client_ip: str
    successfully_forwarded: List[int]
    failed_to_forward: List[int]

class MyStatusResponse(BaseModel):
    """Response model for a specific client's status."""
    my_forwarded_ports: List[int]
    my_allowed_ports: List[int]

# --- Admin-facing Models ---

class AdminStatusResponse(BaseModel):
    """Response model for the overall status of the daemon (admin view)."""
    forwarded_rules: Dict[str, List[int]]
    unavailable_ports_in_range: List[int]
    managed_clients: List[ClientInfoPublic]