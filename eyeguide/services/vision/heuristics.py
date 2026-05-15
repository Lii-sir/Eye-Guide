from __future__ import annotations

import cv2
import numpy as np

from eyeguide.domain.models import DetectionEvent, FrameAnalysis


class HeuristicVisionDetector:
    def __init__(self) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def collect_people_events(
        self,
        frame: np.ndarray,
        analysis: FrameAnalysis,
        frame_index: int,
    ) -> None:
        if frame_index % 4 != 0:
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        rects, _ = self._hog.detectMultiScale(
            gray,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
        )
        height, width = frame.shape[:2]
        for (x, y, w, h) in rects:
            center_x = x + w / 2
            if not (0.25 * width <= center_x <= 0.75 * width):
                continue
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 180, 255), 2)
            priority = 2 if y + h > 0.62 * height else 4
            analysis.events.append(
                DetectionEvent(
                    message="前方检测到行人，请减速通过。",
                    category="person",
                    priority=priority,
                    cooldown_seconds=4.0,
                )
            )

    def collect_ground_obstacle_events(self, frame: np.ndarray, analysis: FrameAnalysis) -> None:
        height, width = frame.shape[:2]
        roi = frame[int(height * 0.55) :, int(width * 0.2) : int(width * 0.8)]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 70, 140)
        kernel = np.ones((5, 5), np.uint8)
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 2200:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if w < roi.shape[1] * 0.14:
                continue

            global_x = x + int(width * 0.2)
            global_y = y + int(height * 0.55)
            cv2.rectangle(frame, (global_x, global_y), (global_x + w, global_y + h), (0, 0, 255), 2)
            analysis.events.append(
                DetectionEvent(
                    message="前方地面区域可能有障碍物，请提前绕行。",
                    category="ground_obstacle",
                    priority=2,
                    cooldown_seconds=4.5,
                )
            )
            break

    def collect_step_events(self, frame: np.ndarray, analysis: FrameAnalysis) -> None:
        height, width = frame.shape[:2]
        roi = frame[int(height * 0.5) :, int(width * 0.15) : int(width * 0.85)]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 150)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=45,
            minLineLength=int(width * 0.12),
            maxLineGap=20,
        )
        if lines is None:
            return

        horizontal_y = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            dx = x2 - x1
            dy = y2 - y1
            if dx == 0:
                continue
            slope = abs(dy / dx)
            line_length = float(np.hypot(dx, dy))
            if slope < 0.18 and line_length > width * 0.15:
                horizontal_y.append((y1 + y2) / 2)

        if len(horizontal_y) < 5:
            return

        spread = max(horizontal_y) - min(horizontal_y)
        if spread < roi.shape[0] * 0.18:
            return

        cv2.putText(
            frame,
            "Possible steps ahead",
            (20, 95),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (0, 255, 255),
            2,
        )
        analysis.events.append(
            DetectionEvent(
                message="前方可能有台阶或高度变化，请减速并确认脚下。",
                category="step_risk",
                priority=1,
                cooldown_seconds=5.0,
            )
        )

