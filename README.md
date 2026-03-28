# Albacore Autonomous Submarine Scaffold

Hackathon scaffold for an untethered UUV software stack with:

- laptop manual control over UDP/WiFi
- Jetson orchestration and autonomy modes
- single Teensy 4.1 firmware scaffold (control + sensing on one MCU)
- mock hardware interfaces for laptop-only development

## Repo Layout

```text
firmware/
  control_teensy/control_teensy.ino
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
pip install -r requirements.txt
```

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
- Serial Jetson↔Teensy (single USB link; baud set in `jetson/config.py` `CONTROL_BAUD`; USB is full-speed):
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

## Notes

- This is intentionally hackathon-speed code: flat config, simple classes, minimal abstractions.
- `jetson/comms/mock_comms.py` is the primary no-hardware development path.
- Teensy sketches are scaffolds and may require pin remapping + library API adjustments on hardware bring-up.
