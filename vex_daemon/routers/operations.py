"""Fleet/pulse/db/ship API endpoints for Vex daemon."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from operations import db_inspect, fleet_status, pulse, ship

router = APIRouter(prefix="/ops", tags=["ops"])


class ShipRequest(BaseModel):
    repo: str
    message: str


@router.get("/fleet")
async def api_fleet():
    return fleet_status()


@router.get("/pulse")
async def api_pulse():
    return pulse()


@router.get("/db")
async def api_db(path: str | None = None):
    return db_inspect(path)


@router.post("/ship")
async def api_ship(req: ShipRequest):
    result = ship(req.repo, req.message)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result
