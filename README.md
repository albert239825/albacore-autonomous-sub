# Albacore Autonomous Submarine Scaffold

Hackathon scaffold for an untethered UUV software stack with:

- laptop manual control over UDP/WiFi
- Jetson orchestration and autonomy modes
- single Teensy 4.1 firmware scaffold (control + sensing on one MCU)
- mock hardware interfaces for laptop-only development

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
