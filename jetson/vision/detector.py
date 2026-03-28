"""YOLOv8n object detector — pure inference and visualization (no camera/stream ownership)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from config import CAMERA_INDEX, VISION_CONF_THRESHOLD, VISION_IOU_THRESHOLD, VISION_TARGET_CLASSES, YOLO_MODEL_NAME


@dataclass(slots=True)
class Detection:
    class_name: str  # COCO class label, e.g. "person", "boat", "sports ball"
    confidence: float  # 0.0 to 1.0
    x: float  # bounding box center x in pixels
    y: float  # bounding box center y in pixels
    w: float  # bounding box width in pixels
    h: float  # bounding box height in pixels


class Detector:
    def __init__(
        self,
        model_name: str = YOLO_MODEL_NAME,
        conf_threshold: float = VISION_CONF_THRESHOLD,
        iou_threshold: float = VISION_IOU_THRESHOLD,
        target_classes: list[str] | None = None,
    ) -> None:
        """
        Load YOLOv8n. On Jetson with CUDA available, ultralytics auto-selects GPU.

        target_classes: classes we consider "follow targets" for visualization and
        downstream filtering defaults. If omitted, uses config.VISION_TARGET_CLASSES.
        """
        from ultralytics import YOLO

        self.model = YOLO(model_name)
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.target_classes: set[str] = set(target_classes) if target_classes is not None else set(VISION_TARGET_CLASSES)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run inference on a single BGR frame (OpenCV format)."""
        results = self.model(
            frame,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            verbose=False,
        )
        result0 = results[0]
        out: list[Detection] = []
        if result0.boxes is None or len(result0.boxes) == 0:
            return out

        names = result0.names
        for box in result0.boxes:
            conf = float(box.conf.item())
            cls_id = int(box.cls.item())
            class_name = str(names[cls_id])
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            w = float(x2 - x1)
            h = float(y2 - y1)
            cx = float((x1 + x2) / 2.0)
            cy = float((y1 + y2) / 2.0)
            out.append(Detection(class_name=class_name, confidence=conf, x=cx, y=cy, w=w, h=h))

        out.sort(key=lambda d: d.confidence, reverse=True)
        return out

    def draw(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        target_classes: Iterable[str] | None = None,
    ) -> np.ndarray:
        """Draw detections on a copy of frame.

        Target-class boxes are red; non-target boxes are green.
        """
        import cv2

        annotated = frame.copy()
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.5
        thickness = 1
        target_set = set(target_classes) if target_classes is not None else self.target_classes
        for det in detections:
            is_target = (not target_set) or (det.class_name in target_set)
            color = (0, 0, 255) if is_target else (0, 255, 0)
            half_w, half_h = det.w / 2.0, det.h / 2.0
            x1 = int(round(det.x - half_w))
            y1 = int(round(det.y - half_h))
            x2 = int(round(det.x + half_w))
            y2 = int(round(det.y + half_h))
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{det.class_name} {det.confidence:.0%}"
            (tw, th), baseline = cv2.getTextSize(label, font, scale, thickness)
            ty = max(0, y1 - th - 4)
            cv2.rectangle(annotated, (x1, ty), (x1 + tw + 2, ty + th + baseline + 2), (0, 0, 0), -1)
            cv2.putText(annotated, label, (x1 + 1, ty + th + 1), font, scale, (255, 255, 255), thickness)
        return annotated


if __name__ == "__main__":
    import cv2

    detector = Detector(
        model_name=YOLO_MODEL_NAME,
        conf_threshold=VISION_CONF_THRESHOLD,
        iou_threshold=VISION_IOU_THRESHOLD,
    )
    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        print(f"ERROR: could not open camera at index {CAMERA_INDEX}")
        raise SystemExit(1)

    frame_count = 0
    fps_sum = 0.0
    prev_t = time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.01)
            continue
        detections = detector.detect(frame)
        annotated = detector.draw(frame, detections)
        now = time.perf_counter()
        dt = max(1e-9, now - prev_t)
        fps = 1.0 / dt
        prev_t = now
        frame_count += 1
        fps_sum += fps
        cv2.putText(
            annotated,
            f"FPS: {fps:.1f}",
            (8, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
        )
        cv2.imshow("Albacore YOLOv8n", annotated)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    if frame_count > 0:
        print(f"Average FPS: {fps_sum / frame_count:.1f}")
    else:
        print("No frames captured.")
