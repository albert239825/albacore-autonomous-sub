import cv2
import sys

device = "/dev/video0"   # change if your camera is /dev/video1, etc.
out = "test_frame.jpg"

cap = cv2.VideoCapture(device, cv2.CAP_V4L2)

if not cap.isOpened():
    print(f"ERROR: could not open {device}")
    sys.exit(1)

# Optional: set a conservative resolution that most UVC cams support
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

ret, frame = cap.read()
cap.release()

if not ret or frame is None:
    print("ERROR: failed to read frame")
    sys.exit(1)

ok = cv2.imwrite(out, frame)
if not ok:
    print("ERROR: failed to save image")
    sys.exit(1)

print(f"Saved {out} with shape {frame.shape}")