# src/main.py

import logging
import uvicorn
from contextlib import asynccontextmanager
from typing import List
from fastapi.security import APIKeyHeader
from fastapi import FastAPI, Request, HTTPException, Security, APIRouter, Depends

from app.core.config import settings
from app.api.models import *
from app.services.portmaster_service import PortMasterService
from app.system.iptables import IPTablesManager
from app.system.scanner import HostPortScanner

# --- Globals & Lifespan ---
service_instance: PortMasterService

@asynccontextmanager
async def lifespan(app: FastAPI):
    global service_instance
    logging.info("Application startup...")
    service_instance = PortMasterService(settings, IPTablesManager(), HostPortScanner())
    await service_instance.initialize()
    yield
    logging.info("Application shutdown.")

app = FastAPI(title="PortMaster API", version="2.0.0", lifespan=lifespan)

# --- SECURITY & DEPENDENCIES ---
admin_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=True)
user_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def get_admin_key(key: str = Security(admin_api_key_header)):
    if key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing Admin API Key")

async def get_current_client(key: str = Security(user_api_key_header)) -> ClientInfo:
    client = service_instance.get_client_by_key(key)
    if not client:
        raise HTTPException(status_code=403, detail="Invalid or missing User API Key")
    return client

# --- ADMIN API ROUTER ---
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(get_admin_key)])

@admin_router.post("/clients", response_model=ClientInfo, status_code=201)
async def create_client(req: ClientCreateRequest):
    """Creates a new client and returns their generated API key."""
    client = await service_instance.create_client(req.client_id, req.port_range)
    if not client:
        raise HTTPException(status_code=400, detail="Client already exists or port range is invalid.")
    return client

@admin_router.get("/clients", response_model=List[ClientInfoPublic])
async def list_clients():
    """Lists all managed clients (without showing their API keys)."""
    clients = service_instance.get_all_clients()
    return [ClientInfoPublic(client_id=c.client_id, allowed_ports=c.allowed_ports) for c in clients]

@admin_router.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: str):
    """Deletes a client from the system."""
    success = await service_instance.delete_client(client_id)
    if not success:
        raise HTTPException(status_code=404, detail="Client not found.")
    return

@admin_router.get("/status", response_model=AdminStatusResponse)
async def get_admin_status():
    """Gets the overall system status."""
    forwarded, unavailable = service_instance.forwarded_ports, service_instance.unavailable_ports
    clients = service_instance.get_all_clients()
    public_clients = [ClientInfoPublic(client_id=c.client_id, allowed_ports=c.allowed_ports) for c in clients]
    return AdminStatusResponse(
        forwarded_rules={ip: sorted(list(p)) for ip, p in forwarded.items()},
        unavailable_ports_in_range=sorted(list(unavailable)),
        managed_clients=public_clients
    )

# --- USER API ROUTER ---
user_router = APIRouter()

@user_router.get("/ports", response_model=MyStatusResponse)
async def get_my_status(request: Request, client: ClientInfo = Depends(get_current_client)):
    """Gets the current status for the authenticated client."""
    my_ports = sorted(list(service_instance.forwarded_ports.get(request.client.host, set())))
    return MyStatusResponse(my_forwarded_ports=my_ports, my_allowed_ports=client.allowed_ports)

@user_router.post("/ports", response_model=PortForwardResponse)
async def update_ports(req: Request, body: PortForwardRequest, client: ClientInfo = Depends(get_current_client)):
    """Updates port forwarding rules for the client from their assigned pool."""
    _, failed = await service_instance.update_client_ports(req.client.host, set(body.ports), client.allowed_ports)
    final_rules = service_instance.forwarded_ports.get(req.client.host, set())
    return PortForwardResponse(
        message="Port forwarding rules updated.",
        client_ip=req.client.host,
        successfully_forwarded=sorted(list(final_rules)),
        failed_to_forward=sorted(list(failed))
    )

@user_router.delete("/ports", status_code=204)
async def disconnect(req: Request, _: ClientInfo = Depends(get_current_client)):
    """Removes all forwarding rules for the client's current IP address."""
    await service_instance.disconnect_client_ip(req.client.host)
    return

# --- INCLUDE ROUTERS ---
app.include_router(admin_router)
app.include_router(user_router)

# --- MAIN ENTRY ---
if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.vpn_ip, port=settings.daemon_port, log_level="info")