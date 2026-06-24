from __future__ import annotations

import sys

import pytest

from cursor_controller import RelativeCursorController


class FakePyAutoGui:
    PAUSE = 0

    def __init__(self) -> None:
        self.moves: list[tuple[int, int, int]] = []
        self.clicks = 0

    def size(self) -> tuple[int, int]:
        return (1000, 500)

    def moveRel(self, dx: int, dy: int, duration: int = 0) -> None:
        self.moves.append((dx, dy, duration))

    def click(self) -> None:
        self.clicks += 1


def test_relative_cursor_controller_moves_by_point_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    controller = RelativeCursorController(
        gain=1.0,
        max_step=200,
        smoothing_alpha=1.0,
        deadzone_px=0.0,
    )

    first_motion = controller.update({"x": 0.4, "y": 0.4})
    assert first_motion is not None
    assert first_motion.dx == 0
    assert first_motion.dy == 0
    motion = controller.update({"x": 0.5, "y": 0.6})

    assert motion is not None
    assert motion.dx == 99
    assert motion.dy == 99
    assert fake_pyautogui.moves == [(99, 99, 0)]


def test_relative_cursor_controller_smooths_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    controller = RelativeCursorController(
        gain=1.0,
        max_step=200,
        smoothing_alpha=0.5,
        deadzone_px=0.0,
    )

    controller.update({"x": 0.0, "y": 0.0})
    motion = controller.update({"x": 0.2, "y": 0.0})

    assert motion is not None
    assert motion.dx == 100
    assert motion.dy == 0


def test_relative_cursor_controller_clamps_large_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    controller = RelativeCursorController(
        gain=10.0,
        max_step=50,
        smoothing_alpha=1.0,
        deadzone_px=0.0,
    )

    controller.update({"x": 0.0, "y": 0.0})
    motion = controller.update({"x": 1.0, "y": 1.0})

    assert motion is not None
    assert motion.dx == 50
    assert motion.dy == 50


def test_relative_cursor_controller_resets_when_point_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    controller = RelativeCursorController(gain=1.0, smoothing_alpha=1.0, deadzone_px=0.0)

    controller.update({"x": 0.0, "y": 0.0})
    assert controller.update(None) is None
    motion = controller.update({"x": 1.0, "y": 1.0})

    assert motion is not None
    assert motion.dx == 0
    assert motion.dy == 0
    assert fake_pyautogui.moves == []


def test_relative_cursor_controller_clicks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    controller = RelativeCursorController()

    controller.click()

    assert fake_pyautogui.clicks == 1

