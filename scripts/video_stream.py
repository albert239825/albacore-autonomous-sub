#!/usr/bin/env python3
"""Minimal MJPEG stream from Jetson camera. Open http://<jetson-ip>:8080 in browser."""

import cv2
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

CAMERA_INDEX = 0
PORT = 8080
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

cap = cv2.VideoCapture(CAMERA_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
lock = threading.Lock()
current_frame = None

def capture_loop():
    global current_frame
    while True:
        ret, frame = cap.read()
        if ret:
            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with lock:
                current_frame = jpeg.tobytes()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        while True:
            with lock:
                frame = current_frame
            if frame is None:
                continue
            try:
                self.wfile.write(b'--frame\r\n')
                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
            except BrokenPipeError:
                break

    def log_message(self, format, *args):
        pass  # suppress noisy logs

if __name__ == '__main__':
    threading.Thread(target=capture_loop, daemon=True).start()
    print(f"Stream at http://0.0.0.0:{PORT}")
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()