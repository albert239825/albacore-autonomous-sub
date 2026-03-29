# Laptop C2 Stack

This folder now has three operator-facing components:

- `backend.py`: FastAPI server (REST + WebSocket + Jetson UDP relay)
- `dashboard/`: React UI for live C2 monitoring and actions
- `controller.py`: legacy/manual UDP keyboard/gamepad controller

## Run Backend

```bash
cd laptop
pip install -r ../requirements.txt
uvicorn backend:app --host 0.0.0.0 --port 5007
```

Optional Jetson target override:

```bash
JETSON_IP=192.168.0.204 JETSON_UDP_PORT=5005 uvicorn backend:app --host 0.0.0.0 --port 5007
```

## Backend Endpoints

- `GET /api/state`: initial dashboard state
- `POST /api/ingest/detection`: Jetson detection photo ingest
  - multipart `file`, `class_name`, `confidence`
- `POST /api/ingest/telemetry`: Jetson telemetry ingest (JSON)
- `POST /api/command`: dashboard commands -> Jetson UDP
  - `{"action":"deploy","target_id":"M-001"}`
  - `{"action":"neutralize","target_id":"M-001"}`
  - `{"action":"manual"}`
  - `{"action":"estop"}`
- `GET /api/captures/{filename}`: captured JPEG file
- `WS /ws`: realtime state updates/events

## Run Dashboard

Dev mode:

```bash
cd laptop/dashboard
npm install
npm run dev
```

Production build (served by `backend.py` from `/`):

```bash
cd laptop/dashboard
npm install
npm run build
cd ..
uvicorn backend:app --host 0.0.0.0 --port 5007
```

## Quick API Smoke Test

From another terminal:

```bash
# state
curl -s http://localhost:5007/api/state

# telemetry ingest
curl -s -X POST http://localhost:5007/api/ingest/telemetry \
  -H "Content-Type: application/json" \
  -d '{"bat":{"voltage":12.1},"mode":"MANUAL","cmd":{"thruster":30,"bow":0,"rudder":10,"elevator":0,"ballast":0}}'

# command relay
curl -s -X POST http://localhost:5007/api/command \
  -H "Content-Type: application/json" \
  -d '{"action":"estop"}'
```
