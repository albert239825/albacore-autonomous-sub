"""Central tuning and wiring constants for the Jetson software stack.

Edit here instead of scattering magic numbers across modules. Serial device
names are Linux defaults (``ttyACM*``); reassign after ``ls /dev/ttyACM*`` on
the robot. Network IPs are for documentation and laptop scripts; the Jetson
listener binds ``UDP_LISTEN_HOST``.
"""

from __future__ import annotations

# --- Serial: single Teensy (CMD + all telemetry including AUD on one USB link) ---
# Linux (Jetson): typically /dev/ttyACM0. macOS: use /dev/cu.usbmodem* (see `ls /dev/cu.usbmodem*`).
CONTROL_SERIAL_PORT = "/dev/ttyACM0"
CONTROL_BAUD = 1_000_000
SERIAL_TIMEOUT_S = 0.01

# --- UDP: laptop commands and dashboard relay (see main.py for client port +1) ---
UDP_LISTEN_HOST = "0.0.0.0"
UDP_LISTEN_PORT = 5005
JETSON_IP = "192.168.1.50"
LAPTOP_IP = "192.168.1.10"
UDP_RECV_BUFFER = 4096

# --- Main loop: should match laptop/controller 20 Hz CMD heartbeat; watchdog matches Teensy firmware ---
MAIN_LOOP_HZ = 20.0
MAIN_LOOP_DT = 1.0 / MAIN_LOOP_HZ
WATCHDOG_TIMEOUT_S = 0.5

# --- Vision: COCO pretrained YOLOv8n; thresholds for NMS/display ---
CAMERA_INDEX = 0
YOLO_MODEL_NAME = "yolov8n.pt"
VISION_CONF_THRESHOLD = 0.25
VISION_IOU_THRESHOLD = 0.45

# --- Audio: nominal hydrophone sample rate from single Teensy (timer ISR); classifier resamples to 16 kHz ---
AUDIO_SAMPLE_RATE_HZ = 5_000
AUDIO_CLASSIFIER_SAMPLE_RATE_HZ = 16_000
AUDIO_CHUNK_SECONDS = 0.25
AUDIO_CHUNK_SAMPLES = int(AUDIO_SAMPLE_RATE_HZ * AUDIO_CHUNK_SECONDS)
AUDIO_BINARY_MODE = False

# # --- TDOA: 15 cm square hydrophone layout (x forward, y starboard); z unused in 2D bearing ---
# ARRAY_GEOMETRY = [
#     (-0.075, 0.075, 0.0),
#     (0.075, 0.075, 0.0),
#     (-0.075, -0.075, 0.0),
#     (0.075, -0.075, 0.0),
# ]
# SOUND_SPEED_MPS = 1480.0

# --- Navigation: waypoint PID, target-follow P gains, depth-hold placeholder ---
# WAYPOINT_KP = 1.5
# WAYPOINT_KI = 0.0
# WAYPOINT_KD = 0.2
# WAYPOINT_CRUISE_THRUSTER = 45
# WAYPOINT_STOP_RADIUS_M = 2.0

# TARGET_FOLLOW_RUDDER_KP = 60.0
# TARGET_FOLLOW_THRUSTER_KP = 60.0
# TARGET_DESIRED_AREA_RATIO = 0.08
# TARGET_LOST_FRAMES_HOLD = 8
# TARGET_LOST_FRAMES_STOP = 20
# TARGET_HOLD_THRUSTER = 20

# DEPTH_KP = 3.0
# DEPTH_KI = 0.0
# DEPTH_KD = 0.2
# DEPTH_DEADBAND_M = 0.10

# --- Actuator command limits (must match protocol.clamp_cmd and firmware) ---
THRUSTER_MIN = -100
THRUSTER_MAX = 100
BOW_MIN = -100
BOW_MAX = 100
RUDDER_MIN_DEG = -45
RUDDER_MAX_DEG = 45
ELEVATOR_MIN_DEG = -45
ELEVATOR_MAX_DEG = 45

# Ballast: signed integer in CMD field (-1 / 0 / 1)
BALLAST_ASCEND = -1
BALLAST_STOP = 0
BALLAST_DESCEND = 1

# --- MockComms defaults (optional; mock module may use inline values too) ---
MOCK_BATTERY_START_V = 12.6
MOCK_BATTERY_END_V = 11.0
MOCK_BATTERY_DRAIN_V_PER_S = 0.001
MOCK_AUDIO_TONE_HZ = 550.0
MOCK_AUDIO_AMPLITUDE = 0.8
MOCK_HEADING_RATE_DEG_PER_S = 8.0
