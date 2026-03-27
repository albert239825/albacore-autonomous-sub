"""YOLOv8n object detector wrapper."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

import cv2
import numpy as np
from ultralytics import YOLO

from config import CAMERA_INDEX, VISION_CONF_THRESHOLD, VISION_IOU_THRESHOLD, YOLO_MODEL_NAME


@dataclass(slots=True)
class Detection:
    cls_name: str
    confidence: float
    xywh: tuple[float, float, float, float]


class YoloDetector:
    def __init__(self, model_name: str = YOLO_MODEL_NAME) -> None:
        self.model = YOLO(model_name)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        result = self.model.predict(frame, conf=VISION_CONF_THRESHOLD, iou=VISION_IOU_THRESHOLD, verbose=False)[0]
        detections: list[Detection] = []
        if result.boxes is None:
            return detections
        names = result.names
        for box in result.boxes:
            cls_id = int(box.cls.item())
            conf = float(box.conf.item())
            x, y, w, h = box.xywh[0].tolist()
            detections.append(Detection(cls_name=str(names[cls_id]), confidence=conf, xywh=(x, y, w, h)))
        return detections


if __name__ == "__main__":
    detector = YoloDetector()
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        raise RuntimeError("Failed to open camera.")

    prev_t = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        detections = detector.detect(frame)

        for det in detections:
            x, y, w, h = det.xywh
            x1, y1 = int(x - w / 2), int(y - h / 2)
            x2, y2 = int(x + w / 2), int(y + h / 2)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                frame,
                f"{det.cls_name} {det.confidence:.2f}",
                (x1, max(0, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

        now = time.time()
        fps = 1.0 / max(1e-6, now - prev_t)
        prev_t = now
        cv2.putText(frame, f"FPS: {fps:.1f}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow("Albacore YOLOv8n", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
