from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Literal, Sequence


THUMB_TIP = 4
INDEX_FINGER_TIP = 8
WRIST = 0
PALM_LANDMARKS = (0, 5, 9, 13, 17)
INDEX_FINGER_MCP = 5
INDEX_FINGER_PIP = 6
MIDDLE_FINGER_MCP = 9
RING_FINGER_MCP = 13
PINKY_MCP = 17
FINGER_MCPS = (5, 9, 13, 17)
FINGER_TIPS = (8, 12, 16, 20)
FINGER_PIPS = (6, 10, 14, 18)
DEFAULT_PINCH_THRESHOLD = 0.55
DEFAULT_MIN_PINCHED_FINGERS = 3
DEFAULT_MIN_PALM_FACING_RATIO = 0.65
MIN_HAND_SCALE = 0.001
HandShape = Literal["open", "closed", "unknown"]


@dataclass(frozen=True)
class NormalizedPoint:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class PinchResult:
    active: bool
    distance: float
    midpoint: NormalizedPoint
    confidence: float
    hand_scale: float
    palm_facing: bool
    palm_facing_score: float


@dataclass(frozen=True)
class HandShapeResult:
    shape: HandShape
    extended_fingers: int
    confidence: float


@dataclass(frozen=True)
class IndexPointingResult:
    active: bool
    point: NormalizedPoint
    confidence: float
    index_score: float
    other_finger_score: float


def point_from_landmark(landmark: object) -> NormalizedPoint:
    return NormalizedPoint(
        x=float(getattr(landmark, "x")),
        y=float(getattr(landmark, "y")),
        z=float(getattr(landmark, "z", 0.0)),
    )


def distance_2d(a: NormalizedPoint, b: NormalizedPoint) -> float:
    return sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def midpoint(a: NormalizedPoint, b: NormalizedPoint) -> NormalizedPoint:
    return NormalizedPoint(
        x=(a.x + b.x) / 2.0,
        y=(a.y + b.y) / 2.0,
        z=(a.z + b.z) / 2.0,
    )


def centroid(points: Sequence[NormalizedPoint]) -> NormalizedPoint:
    if not points:
        raise ValueError("Expected at least one point")

    count = len(points)
    return NormalizedPoint(
        x=sum(point.x for point in points) / count,
        y=sum(point.y for point in points) / count,
        z=sum(point.z for point in points) / count,
    )


def palm_center(landmarks: Sequence[object]) -> NormalizedPoint:
    if len(landmarks) <= max(PALM_LANDMARKS):
        raise ValueError("Expected at least 18 hand landmarks")

    return centroid([point_from_landmark(landmarks[index]) for index in PALM_LANDMARKS])


def palm_facing_score(landmarks: Sequence[object]) -> float:
    if len(landmarks) <= max(WRIST, INDEX_FINGER_MCP, MIDDLE_FINGER_MCP, PINKY_MCP):
        raise ValueError("Expected at least 18 hand landmarks")

    wrist = point_from_landmark(landmarks[WRIST])
    middle_mcp = point_from_landmark(landmarks[MIDDLE_FINGER_MCP])
    index_mcp = point_from_landmark(landmarks[INDEX_FINGER_MCP])
    pinky_mcp = point_from_landmark(landmarks[PINKY_MCP])
    palm_height = max(distance_2d(wrist, middle_mcp), MIN_HAND_SCALE)
    palm_width = distance_2d(index_mcp, pinky_mcp)
    return palm_width / palm_height


def detect_pinch(
    landmarks: Sequence[object],
    threshold: float = DEFAULT_PINCH_THRESHOLD,
    min_pinched_fingers: int = DEFAULT_MIN_PINCHED_FINGERS,
    min_palm_facing_ratio: float = DEFAULT_MIN_PALM_FACING_RATIO,
) -> PinchResult:
    if len(landmarks) <= max(THUMB_TIP, *FINGER_TIPS):
        raise ValueError("Expected at least 21 hand landmarks")
    if not 1 <= min_pinched_fingers <= len(FINGER_TIPS):
        raise ValueError("min_pinched_fingers must be between 1 and 4")

    thumb_tip = point_from_landmark(landmarks[THUMB_TIP])
    wrist = point_from_landmark(landmarks[WRIST])
    middle_mcp = point_from_landmark(landmarks[MIDDLE_FINGER_MCP])
    hand_scale = max(distance_2d(wrist, middle_mcp), MIN_HAND_SCALE)
    finger_tips = [point_from_landmark(landmarks[index]) for index in FINGER_TIPS]
    raw_distances = [distance_2d(thumb_tip, finger_tip) for finger_tip in finger_tips]
    distances = [distance / hand_scale for distance in raw_distances]
    close_distances = [distance for distance in distances if distance < threshold]
    facing_score = palm_facing_score(landmarks)
    palm_facing = facing_score >= min_palm_facing_ratio
    active = len(close_distances) >= min_pinched_fingers
    sorted_distances = sorted(distances)
    pinch_distance = sorted_distances[min_pinched_fingers - 1]
    tracked_finger_tips = [
        finger_tip
        for finger_tip, distance in zip(finger_tips, distances)
        if distance < threshold
    ]
    midpoint_points = [thumb_tip, *tracked_finger_tips] if tracked_finger_tips else [thumb_tip]

    if threshold <= 0:
        confidence = 0.0
    else:
        confidence = max(0.0, min(1.0, 1.0 - (pinch_distance / threshold)))

    return PinchResult(
        active=active,
        distance=pinch_distance,
        midpoint=centroid(midpoint_points),
        confidence=confidence,
        hand_scale=hand_scale,
        palm_facing=palm_facing,
        palm_facing_score=facing_score,
    )


def index_fingertip(landmarks: Sequence[object]) -> NormalizedPoint:
    if len(landmarks) <= INDEX_FINGER_TIP:
        raise ValueError("Expected at least 9 hand landmarks")

    return point_from_landmark(landmarks[INDEX_FINGER_TIP])


def cursor_point(
    landmarks: Sequence[object],
    *,
    tip_weight: float = 0.70,
) -> NormalizedPoint:
    if len(landmarks) <= max(INDEX_FINGER_TIP, INDEX_FINGER_PIP):
        raise ValueError("Expected at least 9 hand landmarks")
    if not 0.0 <= tip_weight <= 1.0:
        raise ValueError("tip_weight must be in [0.0, 1.0]")

    tip = point_from_landmark(landmarks[INDEX_FINGER_TIP])
    pip = point_from_landmark(landmarks[INDEX_FINGER_PIP])
    pip_weight = 1.0 - tip_weight
    return NormalizedPoint(
        x=tip.x * tip_weight + pip.x * pip_weight,
        y=tip.y * tip_weight + pip.y * pip_weight,
        z=tip.z * tip_weight + pip.z * pip_weight,
    )


def is_pointing_gesture(gesture: str, min_score: float = 0.35, score: float = 1.0) -> bool:
    return gesture == "Pointing_Up" and score >= min_score


def detect_index_pointing(
    landmarks: Sequence[object],
    *,
    min_index_score: float = 0.74,
    max_other_finger_score: float = 0.86,
) -> IndexPointingResult:
    if len(landmarks) <= max(*FINGER_MCPS, *FINGER_PIPS, *FINGER_TIPS):
        raise ValueError("Expected at least 21 hand landmarks")

    wrist = point_from_landmark(landmarks[WRIST])
    middle_mcp = point_from_landmark(landmarks[MIDDLE_FINGER_MCP])
    hand_scale = max(distance_2d(wrist, middle_mcp), MIN_HAND_SCALE)
    index_score = finger_extension_score(
        point_from_landmark(landmarks[INDEX_FINGER_MCP]),
        point_from_landmark(landmarks[INDEX_FINGER_PIP]),
        point_from_landmark(landmarks[INDEX_FINGER_TIP]),
        hand_scale,
    )
    other_scores = [
        finger_extension_score(
            point_from_landmark(landmarks[mcp]),
            point_from_landmark(landmarks[pip]),
            point_from_landmark(landmarks[tip]),
            hand_scale,
        )
        for mcp, pip, tip in zip(
            (MIDDLE_FINGER_MCP, RING_FINGER_MCP, PINKY_MCP),
            (10, 14, 18),
            (12, 16, 20),
        )
    ]
    other_finger_score = max(other_scores)
    active = index_score >= min_index_score and other_finger_score <= max_other_finger_score
    confidence = max(
        0.0,
        min(
            1.0,
            ((index_score - min_index_score) / (1.0 - min_index_score))
            * ((max_other_finger_score - other_finger_score) / max_other_finger_score),
        ),
    )

    return IndexPointingResult(
        active=active,
        point=index_fingertip(landmarks),
        confidence=confidence if active else 0.0,
        index_score=index_score,
        other_finger_score=other_finger_score,
    )


def finger_extension_score(
    mcp: NormalizedPoint,
    pip: NormalizedPoint,
    tip: NormalizedPoint,
    hand_scale: float,
) -> float:
    segment_length = distance_2d(mcp, pip) + distance_2d(pip, tip)
    if segment_length <= MIN_HAND_SCALE:
        return 0.0
    tip_distance = distance_2d(mcp, tip)
    straightness = tip_distance / segment_length
    relative_length = tip_distance / max(hand_scale * 0.55, MIN_HAND_SCALE)
    return max(0.0, min(1.0, straightness * min(1.0, relative_length)))


def detect_hand_shape(landmarks: Sequence[object]) -> HandShapeResult:
    if len(landmarks) <= max(WRIST, *FINGER_TIPS, *FINGER_PIPS):
        raise ValueError("Expected at least 21 hand landmarks")

    wrist = point_from_landmark(landmarks[WRIST])
    extended_count = 0

    for tip_index, pip_index in zip(FINGER_TIPS, FINGER_PIPS):
        tip = point_from_landmark(landmarks[tip_index])
        pip = point_from_landmark(landmarks[pip_index])
        tip_distance = distance_2d(wrist, tip)
        pip_distance = distance_2d(wrist, pip)

        if tip_distance > pip_distance * 1.12:
            extended_count += 1

    if extended_count >= 3:
        return HandShapeResult(
            shape="open",
            extended_fingers=extended_count,
            confidence=extended_count / len(FINGER_TIPS),
        )

    if extended_count <= 1:
        return HandShapeResult(
            shape="closed",
            extended_fingers=extended_count,
            confidence=(len(FINGER_TIPS) - extended_count) / len(FINGER_TIPS),
        )

    return HandShapeResult(
        shape="unknown",
        extended_fingers=extended_count,
        confidence=0.5,
    )
