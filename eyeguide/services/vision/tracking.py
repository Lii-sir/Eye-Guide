from __future__ import annotations

from dataclasses import dataclass

from eyeguide.domain.models import DetectionBox


@dataclass
class _TrackState:
    track_id: int
    label: str
    box: tuple[int, int, int, int]
    confidence: float
    missed_frames: int = 0


class ObjectTracker:
    def __init__(self, iou_threshold: float = 0.28, max_missed_frames: int = 10) -> None:
        self._iou_threshold = iou_threshold
        self._max_missed_frames = max_missed_frames
        self._next_track_id = 1
        self._tracks: list[_TrackState] = []

    def update(self, detections: list[DetectionBox]) -> list[DetectionBox]:
        unmatched_track_ids = {track.track_id for track in self._tracks}

        for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
            matched = self._match_track(detection, unmatched_track_ids)
            if matched is None:
                matched = self._create_track(detection)
            else:
                matched.label = detection.label
                matched.box = detection.box
                matched.confidence = detection.confidence
                matched.missed_frames = 0
                unmatched_track_ids.discard(matched.track_id)

            detection.track_id = matched.track_id

        for track in self._tracks:
            if track.track_id in unmatched_track_ids:
                track.missed_frames += 1

        self._tracks = [
            track for track in self._tracks if track.missed_frames <= self._max_missed_frames
        ]
        return detections

    def _create_track(self, detection: DetectionBox) -> _TrackState:
        track = _TrackState(
            track_id=self._next_track_id,
            label=detection.label,
            box=detection.box,
            confidence=detection.confidence,
        )
        self._next_track_id += 1
        self._tracks.append(track)
        return track

    def _match_track(
        self,
        detection: DetectionBox,
        unmatched_track_ids: set[int],
    ) -> _TrackState | None:
        best_track: _TrackState | None = None
        best_iou = 0.0
        for track in self._tracks:
            if track.track_id not in unmatched_track_ids or track.label != detection.label:
                continue
            iou = self._iou(track.box, detection.box)
            if iou >= self._iou_threshold and iou > best_iou:
                best_iou = iou
                best_track = track
        return best_track

    def _iou(
        self,
        left: tuple[int, int, int, int],
        right: tuple[int, int, int, int],
    ) -> float:
        left_x1, left_y1, left_x2, left_y2 = left
        right_x1, right_y1, right_x2, right_y2 = right

        intersection_x1 = max(left_x1, right_x1)
        intersection_y1 = max(left_y1, right_y1)
        intersection_x2 = min(left_x2, right_x2)
        intersection_y2 = min(left_y2, right_y2)

        intersection_w = max(0, intersection_x2 - intersection_x1)
        intersection_h = max(0, intersection_y2 - intersection_y1)
        intersection_area = intersection_w * intersection_h
        if intersection_area == 0:
            return 0.0

        left_area = max(0, left_x2 - left_x1) * max(0, left_y2 - left_y1)
        right_area = max(0, right_x2 - right_x1) * max(0, right_y2 - right_y1)
        union_area = left_area + right_area - intersection_area
        if union_area <= 0:
            return 0.0
        return intersection_area / union_area
