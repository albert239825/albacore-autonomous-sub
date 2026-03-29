# Albacore Autonomous Submarine Scaffold

Hackathon scaffold for an untethered UUV software stack with:

- laptop manual control over UDP/WiFi
- Jetson orchestration and autonomy modes
- single Teensy 4.1 firmware scaffold (control + sensing on one MCU)
- mock hardware interfaces for laptop-only development
- laptop-hosted C2 backend + web dashboard for detections, telemetry, and command relay

## Repo Layout

```text
firmware/
  teensy/teensy.ino
jetson/
  config.py
  main.py
  comms/
  audio/
  vision/
  nav/
laptop/
  controller.py
scripts/
  test_serial.py
  teensy_serial_smoke.py
  record_audio.py
  viz_sensors.py
sim/
  swarm_demo/
```

## Quick Start

1. Create venv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
```

Non-Jetson (desktop/laptop) install:

```bash
pip install -r requirements.txt
```

Jetson Orin Nano / JetPack 6.2.x / CUDA 12.6 / Python 3.10 install (**order matters**):

```bash
pip install -r requirements-jetson.txt
pip install --no-deps -r requirements.txt
```

Why this order is required:
- `requirements-jetson.txt` pins Jetson-compatible CUDA wheels for `torch` and `torchvision` from the Jetson index.
- The second step uses `--no-deps` so packages like `ultralytics` do not pull a CPU `torch` wheel from PyPI and overwrite the Jetson wheel.
- Do not include `-r requirements.txt` inside `requirements-jetson.txt`; that can cause pip to resolve Torch from PyPI too early.

2. Start Jetson stack in mock mode:

```bash
cd jetson
python main.py --mock
```

3. Start laptop controller:

```bash
cd laptop
python controller.py --jetson-ip 127.0.0.1 --port 5005
```

## Protocol Summary

- UDP laptop→Jetson:
  - `CMD,thruster_pct,bow_pct,rudder_deg,elevator_deg,ballast_dir`
  - `MODE,MANUAL|AUTO_WAYPOINT|AUTO_TRACK`
  - `ESTOP`
- Serial Jetson↔Teensy (single hardware UART link; baud set in `jetson/config.py` `CONTROL_BAUD`):
  - Out: `CMD,thruster_pct,bow_pct,rudder_deg,elevator_deg,ballast_dir`
  - In: `IMU,...` `USS,...` `BAT,...` `DEP,...` `AUD,...`

## C2 MVP Data Flow

The C2 MVP adds a push-based Jetson -> laptop path while preserving existing UDP manual control:

- **Jetson control loop (`jetson/main.py`)**
  - Still runs at 20Hz for Teensy watchdog-safe `CMD` output.
  - Stores latest `IMU/USS/BAT/DEP` messages from Teensy and pushes compact telemetry JSON to laptop at `TELEMETRY_PUSH_HZ` (default 5Hz).
  - Continues to accept `CMD/MODE/ESTOP` over UDP port `5005`.
- **Jetson vision capture (`jetson/vision/capture.py`)**
  - Runs inside vision thread, not main loop.
  - Requires consecutive high-confidence frames (`CAPTURE_CONFIRM_FRAMES`, `CAPTURE_CONFIDENCE_THRESHOLD`) and cooldown (`CAPTURE_COOLDOWN_S`) before uploading a detection image.
- **Non-blocking HTTP (`jetson/http_sender.py`)**
  - All telemetry and detection uploads are queued and sent from a daemon worker thread.
  - Control/vision loops never block on network I/O; oldest queued requests are dropped first when back-pressured.
- **Laptop backend (`laptop/backend.py`)**
  - REST ingest + state + commands, WebSocket broadcast, photo serving from `laptop/captures/`.
  - Forwards dashboard commands to Jetson via UDP (`MODE,AUTO_TRACK`, `MODE,MANUAL`, `ESTOP`).
- **Dashboard (`laptop/dashboard/`)**
  - Vite React app with live feed panel (Jetson MJPEG), map, telemetry, threat details, and toast alerts.
  - Built assets can be served directly by FastAPI from `laptop/dashboard/dist`.

## C2 MVP Quick Run

### 1) Install dependencies

From repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Start laptop backend

```bash
cd laptop
uvicorn backend:app --host 0.0.0.0 --port 5007
```

Optional environment overrides:

```bash
JETSON_IP=172.20.10.10 JETSON_UDP_PORT=5005 uvicorn backend:app --host 0.0.0.0 --port 5007
```

### 3) Build/serve dashboard

Development:

```bash
cd laptop/dashboard
npm install
npm run dev
```

Production (served by backend at `/`):

```bash
cd laptop/dashboard
npm install
npm run build
cd ..
uvicorn backend:app --host 0.0.0.0 --port 5007
```

### 4) Start Jetson orchestrator

Mock mode on same machine:

```bash
cd jetson
python main.py --mock
```

Hardware mode:

```bash
cd jetson
python main.py
```

Adjust Jetson-side network constants in `jetson/config.py`:

- `LAPTOP_IP` (backend host for ingest)
- `BACKEND_PORT` (default `5007`)

## Backend API Summary

`laptop/backend.py` exposes:

- `GET /api/state` -> full in-memory state (`subs`, `contacts`)
- `POST /api/ingest/detection` -> Jetson image + metadata ingest
  - multipart fields: `file`, `class_name`, `confidence`
- `POST /api/ingest/telemetry` -> Jetson telemetry ingest (JSON)
- `POST /api/command` -> dashboard command relay to Jetson UDP
  - actions: `deploy`, `neutralize`, `manual`, `estop`
- `GET /api/captures/{filename}` -> captured detection image file
- `WS /ws` -> push channel (`telemetry`, `new_contact`, `status_change`, `mode_change`, `estop`)

## Telemetry Payload Shape (Jetson -> Laptop)

`jetson/main.py` sends this schema to `/api/ingest/telemetry`:

```json
{
  "imu": { "ax": 0.0, "ay": 0.0, "az": 9.81, "gx": 0.0, "gy": 0.0, "gz": 0.0 },
  "uss": { "top": -1, "left": -1, "right": -1, "front": -1 },
  "bat": { "voltage": 12.3 },
  "dep": { "depth_m": 1.2 },
  "cmd": { "thruster": 0, "bow": 0, "rudder": 0, "elevator": 0, "ballast": 0 },
  "mode": "MANUAL"
}
```

## Module Smoke Tests

From repo root:

```bash
python -m jetson.comms.protocol
python -m jetson.comms.mock_comms
python -m jetson.audio.tdoa
python -m jetson.nav.target_follow
```

From `jetson/`:

```bash
python -m vision.detector
python -m audio.classifier
python main.py --mock
```

## Teensy hardware bring-up (UART + optional USB debug)

1. **PlatformIO** (repo root): build/upload `teensy41` — see [`platformio.ini`](platformio.ini). Source: [`firmware/teensy/teensy.ino`](firmware/teensy/teensy.ino).
2. **Jetson↔Teensy UART wiring**: Teensy TX → Jetson RX, Teensy RX → Jetson TX, and shared GND (3.3V logic). Jetson header UART is `/dev/ttyTHS1` (pins 8/10).
3. **Jetson serial config**: set `CONTROL_SERIAL_PORT` in [`jetson/config.py`](jetson/config.py) to `/dev/ttyTHS1`.
4. **Optional USB smoke test from laptop/host** (repo root, venv, close PlatformIO monitor first):

   ```bash
   export TEENSY_SERIAL_PORT=/dev/cu.usbmodemXXXXXXXX
   python scripts/teensy_serial_smoke.py --seconds 5
   python scripts/teensy_serial_smoke.py --watchdog-test
   ```

   This smoke script is for direct USB debug (`/dev/cu.usbmodem*` or `/dev/ttyACM*`), not the production Jetson UART path. Defaults `AUD` off in the console; add `--show-aud` only if you need it. Optional firmware debug lines: build env `teensy41_debug` (prints `DBG,CMD_ACK,...` and `DBG,WD` when watchdog fires).
5. **Full stack on hardware**: wire Teensy UART to Jetson (`/dev/ttyTHS1`) → run `cd jetson && python main.py` (no `--mock`) on Jetson → run `cd laptop && python controller.py --jetson-ip <jetson-ip>` on laptop.

## Notes

- This is intentionally hackathon-speed code: flat config, simple classes, minimal abstractions.
- `jetson/comms/mock_comms.py` is the primary no-hardware development path.
- Teensy sketches are scaffolds and may require pin remapping + library API adjustments on hardware bring-up.
