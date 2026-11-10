import math
from typing import Any, Callable

from .config import Settings
from .mavlink import MavlinkController
from .safety import SafetyPolicy


def _number(arguments: dict[str, Any], name: str) -> float:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _exact_keys(arguments: dict[str, Any], expected: set[str]) -> None:
    if set(arguments) != expected:
        raise ValueError(
            f"Expected exactly these arguments: {', '.join(sorted(expected))}"
        )


class DroneTools:
    def __init__(
        self,
        controller: MavlinkController,
        settings: Settings,
        policy: SafetyPolicy,
    ) -> None:
        self.controller = controller
        self.settings = settings
        self.policy = policy
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "get_telemetry": self._get_telemetry,
            "takeoff": self._takeoff,
            "move_relative": self._move_relative,
            "yaw_rotate": self._yaw_rotate,
            "land": self._land,
        }

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_telemetry",
                    "description": (
                        "Read mode, armed state, GPS position, altitude, speed, "
                        "attitude, GPS quality, and battery telemetry."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "takeoff",
                    "description": (
                        "Arm if needed and request GUIDED takeoff. Altitude is "
                        "in meters above the takeoff point; requires operator approval."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "alt_meters": {
                                "type": "number",
                                "description": (
                                    "Takeoff altitude in meters, within the "
                                    "configured safety limit."
                                ),
                            }
                        },
                        "required": ["alt_meters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_relative",
                    "description": (
                        "Request a single body-relative position target in "
                        "meters. Positive forward_back is forward, positive "
                        "left_right is right, and positive up_down is upward. "
                        "Requires GUIDED mode and operator approval."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "forward_back": {"type": "number"},
                            "left_right": {"type": "number"},
                            "up_down": {"type": "number"},
                        },
                        "required": [
                            "forward_back",
                            "left_right",
                            "up_down",
                        ],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "yaw_rotate",
                    "description": (
                        "Rotate relative to current heading by a bounded number "
                        "of degrees to the left or right. Requires GUIDED mode "
                        "and operator approval."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "degrees": {"type": "number"},
                            "direction": {
                                "type": "string",
                                "enum": ["left", "right"],
                            },
                        },
                        "required": ["degrees", "direction"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "land",
                    "description": (
                        "Request the autopilot's LAND mode. This is a landing "
                        "request, not a guarantee of landing; requires operator approval."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: Any) -> Any:
        if name not in self.handlers:
            raise ValueError(f"Unknown drone tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        return self.handlers[name](arguments)

    def _get_telemetry(self, arguments: dict[str, Any]) -> Any:
        _exact_keys(arguments, set())
        return self.controller.get_telemetry()

    def _takeoff(self, arguments: dict[str, Any]) -> Any:
        _exact_keys(arguments, {"alt_meters"})
        altitude = _number(arguments, "alt_meters")
        self.policy.require_bound(
            "alt_meters", altitude, self.settings.max_altitude_m
        )
        if altitude <= 0:
            raise ValueError("alt_meters must be greater than zero")
        self.controller.ensure_guided()
        self.policy.authorize("takeoff", {"alt_meters": altitude})
        return self.controller.takeoff(altitude)

    def _move_relative(self, arguments: dict[str, Any]) -> Any:
        names = {"forward_back", "left_right", "up_down"}
        _exact_keys(arguments, names)
        forward = _number(arguments, "forward_back")
        lateral = _number(arguments, "left_right")
        vertical = _number(arguments, "up_down")
        self.policy.require_bound("forward_back", forward, self.settings.max_move_m)
        self.policy.require_bound("left_right", lateral, self.settings.max_move_m)
        self.policy.require_bound(
            "up_down", vertical, self.settings.max_vertical_move_m
        )
        distance = math.sqrt(forward**2 + lateral**2 + vertical**2)
        self.policy.require_bound("total move distance", distance, self.settings.max_move_m)
        if distance == 0:
            raise ValueError("At least one movement component must be non-zero")
        self.controller.ensure_guided()
        values = {
            "forward_back": forward,
            "left_right": lateral,
            "up_down": vertical,
        }
        self.policy.authorize("move_relative", values)
        return self.controller.move_relative(forward, lateral, vertical)

    def _yaw_rotate(self, arguments: dict[str, Any]) -> Any:
        _exact_keys(arguments, {"degrees", "direction"})
        degrees = _number(arguments, "degrees")
        direction = arguments.get("direction")
        if direction not in {"left", "right"}:
            raise ValueError("direction must be 'left' or 'right'")
        self.policy.require_bound(
            "degrees", degrees, self.settings.max_yaw_degrees
        )
        if degrees <= 0:
            raise ValueError("degrees must be greater than zero")
        self.controller.ensure_guided()
        self.policy.authorize(
            "yaw_rotate", {"degrees": degrees, "direction": direction}
        )
        return self.controller.yaw_rotate(degrees, direction)

    def _land(self, arguments: dict[str, Any]) -> Any:
        _exact_keys(arguments, set())
        self.controller.ensure_land_available()
        self.policy.authorize("land", {})
        return self.controller.land()
