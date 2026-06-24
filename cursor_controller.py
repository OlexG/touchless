from __future__ import annotations

import ctypes
import subprocess
from dataclasses import dataclass

APPLICATION_SERVICES_PATH = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)


def is_accessibility_trusted() -> bool:
    try:
        application_services = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES_PATH)
        application_services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(application_services.AXIsProcessTrusted())
    except Exception:
        return False


def open_accessibility_settings() -> None:
    try:
        subprocess.Popen(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


@dataclass(frozen=True)
class CursorMotion:
    dx: int
    dy: int


class RelativeCursorController:
    def __init__(
        self,
        *,
        gain: float = 0.65,
        max_step: int = 70,
        smoothing_alpha: float = 0.20,
        deadzone_px: float = 2.0,
    ) -> None:
        if gain <= 0:
            raise ValueError("gain must be > 0")
        if max_step < 1:
            raise ValueError("max_step must be >= 1")
        if not 0.0 < smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0.0, 1.0]")
        if deadzone_px < 0:
            raise ValueError("deadzone_px must be >= 0")

        import pyautogui

        self._pyautogui = pyautogui
        self._pyautogui.PAUSE = 0
        self._gain = gain
        self._max_step = max_step
        self._smoothing_alpha = smoothing_alpha
        self._deadzone_px = deadzone_px
        self._previous_point: tuple[float, float] | None = None
        self._smoothed_dx = 0.0
        self._smoothed_dy = 0.0
        self._carry_dx = 0.0
        self._carry_dy = 0.0
        self._screen_width, self._screen_height = self._pyautogui.size()

    def update(self, point: dict[str, float] | None) -> CursorMotion | None:
        if point is None:
            self.reset()
            return None

        current = (point["x"], point["y"])
        if self._previous_point is None:
            self._previous_point = current
            return CursorMotion(0, 0)

        previous_x, previous_y = self._previous_point
        current_x, current_y = current
        self._previous_point = current

        raw_dx = (current_x - previous_x) * self._screen_width * self._gain
        raw_dy = (current_y - previous_y) * self._screen_height * self._gain
        self._smoothed_dx += self._smoothing_alpha * (raw_dx - self._smoothed_dx)
        self._smoothed_dy += self._smoothing_alpha * (raw_dy - self._smoothed_dy)

        if abs(self._smoothed_dx) < self._deadzone_px:
            self._smoothed_dx = 0.0
        if abs(self._smoothed_dy) < self._deadzone_px:
            self._smoothed_dy = 0.0

        dx_float = self._smoothed_dx + self._carry_dx
        dy_float = self._smoothed_dy + self._carry_dy
        dx = int(dx_float)
        dy = int(dy_float)
        self._carry_dx = dx_float - dx
        self._carry_dy = dy_float - dy
        dx = _clamp(dx, -self._max_step, self._max_step)
        dy = _clamp(dy, -self._max_step, self._max_step)

        if dx or dy:
            self._pyautogui.moveRel(dx, dy, duration=0)

        return CursorMotion(dx, dy)

    def reset(self) -> None:
        self._previous_point = None
        self._smoothed_dx = 0.0
        self._smoothed_dy = 0.0
        self._carry_dx = 0.0
        self._carry_dy = 0.0

    def click(self) -> None:
        self._pyautogui.click()


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
