"""ALBACORE C2 backend: REST + WebSocket + UDP command forwarding."""

from __future__ import annotations

import json
import os
import random
import socket
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parent
CAPTURES_DIR = ROOT_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

JETSON_IP = os.getenv("JETSON_IP", "192.168.0.204")
JETSON_UDP_PORT = int(os.getenv("JETSON_UDP_PORT", "5005"))

next_contact_num = 4

subs: dict[str, dict[str, Any]] = {
    "ALBACORE-1": {
        "status": "deployed",
        "lat": 39.890,
        "lon": -75.170,
        "battery_v": 0.0,
        "mode": "MANUAL",
        "telemetry": {},
    }
}

contacts: dict[str, dict[str, Any]] = {
    "M-001": {
        "lat": 39.891,
        "lon": -75.168,
        "status": "suspected",
        "label": "Limpet Mine",
        "confidence": 0.0,
        "image": None,
        "notes": "Reported by patrol vessel, unconfirmed",
    },
    "M-002": {
        "lat": 39.889,
        "lon": -75.171,
        "status": "suspected",
        "label": "Moored Mine",
        "confidence": 0.0,
        "image": None,
        "notes": "Sonar contact from surface vessel",
    },
    "M-003": {
        "lat": 39.890,
        "lon": -75.165,
        "status": "suspected",
        "label": "Bottom Mine",
        "confidence": 0.0,
        "image": None,
        "notes": "Historical minefield area, high probability",
    },
}

connected_clients: list[WebSocket] = []


async def broadcast(message: dict[str, Any]) -> None:
    data = json.dumps(message)
    disconnected: list[WebSocket] = []
    for ws in connected_clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in connected_clients:
            connected_clients.remove(ws)


def _udp_send(message: str) -> None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(message.encode("ascii"), (JETSON_IP, JETSON_UDP_PORT))
        sock.close()
    except Exception:
        pass


@app.get("/api/state")
async def get_state() -> dict[str, Any]:
    return {"subs": subs, "contacts": contacts}


@app.post("/api/ingest/detection")
async def ingest_detection(
    file: UploadFile,
    class_name: str = Form(...),
    confidence: float = Form(...),
) -> dict[str, str]:
    global next_contact_num

    filename = f"M-{next_contact_num:03d}_{int(time.time())}.jpg"
    filepath = CAPTURES_DIR / filename
    content = await file.read()
    filepath.write_bytes(content)

    contact_id = f"M-{next_contact_num:03d}"
    next_contact_num += 1

    sub = subs["ALBACORE-1"]
    contacts[contact_id] = {
        "lat": sub["lat"] + random.uniform(-0.0002, 0.0002),
        "lon": sub["lon"] + random.uniform(-0.0002, 0.0002),
        "status": "confirmed",
        "label": class_name,
        "confidence": round(confidence, 3),
        "image": filename,
        "notes": f"Detected by ALBACORE-1 at {time.strftime('%H:%M:%S')}",
    }

    await broadcast({"type": "new_contact", "id": contact_id, **contacts[contact_id]})
    return {"id": contact_id}


@app.post("/api/ingest/telemetry")
async def ingest_telemetry(data: dict[str, Any]) -> dict[str, bool]:
    sub = subs["ALBACORE-1"]
    sub["telemetry"] = data
    sub["battery_v"] = data.get("bat", {}).get("voltage", sub["battery_v"])
    sub["mode"] = data.get("mode", sub["mode"])
    await broadcast({"type": "telemetry", **data})
    return {"ok": True}


@app.post("/api/command")
async def post_command(body: dict[str, Any]) -> dict[str, bool]:
    action = body.get("action")
    target_id = body.get("target_id")

    if action == "deploy" and target_id and target_id in contacts:
        contacts[target_id]["status"] = "tracking"
        subs["ALBACORE-1"]["mode"] = "AUTO_TRACK"
        _udp_send("MODE,AUTO_TRACK\n")
        await broadcast({"type": "status_change", "id": target_id, "status": "tracking"})
        await broadcast({"type": "mode_change", "mode": "AUTO_TRACK"})

    elif action == "neutralize" and target_id and target_id in contacts:
        contacts[target_id]["status"] = "neutralized"
        await broadcast({"type": "status_change", "id": target_id, "status": "neutralized"})

    elif action == "manual":
        subs["ALBACORE-1"]["mode"] = "MANUAL"
        _udp_send("MODE,MANUAL\n")
        await broadcast({"type": "mode_change", "mode": "MANUAL"})

    elif action == "estop":
        _udp_send("ESTOP\n")
        await broadcast({"type": "estop"})

    return {"ok": True}


@app.get("/api/captures/{filename}")
async def get_capture(filename: str):
    filepath = CAPTURES_DIR / filename
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "not found"})
    return FileResponse(str(filepath), media_type="image/jpeg")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    connected_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in connected_clients:
            connected_clients.remove(ws)
    except Exception:
        if ws in connected_clients:
            connected_clients.remove(ws)


dashboard_dist = ROOT_DIR / "dashboard" / "dist"
if dashboard_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(dashboard_dist), html=True), name="dashboard")
