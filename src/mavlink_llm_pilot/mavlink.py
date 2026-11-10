import math
import time
from typing import Any

from pymavlink import mavutil

from .config import Settings
from .safety import SafetyError


class MavlinkController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connection: Any = None
        self._messages: dict[str, tuple[Any, float]] = {}

    def connect(self) -> None:
        self.connection = mavutil.mavlink_connection(
            self.settings.mavlink_connection
        )
        heartbeat = self.connection.wait_heartbeat(
            timeout=self.settings.heartbeat_timeout_s
        )
        if heartbeat is None:
            raise TimeoutError(
                "No MAVLink heartbeat received from "
                f"{self.settings.mavlink_connection}"
            )
        self._remember(heartbeat)
        print(
            "Connected to MAVLink system "
            f"{self.connection.target_system}, component "
            f"{self.connection.target_component}."
        )

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def _require_connection(self) -> Any:
        if self.connection is None:
            raise RuntimeError("MAVLink connection is not open")
        return self.connection

    def _remember(self, message: Any) -> None:
        self._messages[message.get_type()] = (message, time.monotonic())

    def _receive_for(self, duration_s: float) -> None:
        connection = self._require_connection()
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            message = connection.recv_match(
                blocking=True, timeout=max(0.01, deadline - time.monotonic())
            )
            if message is not None:
                self._remember(message)

    def _latest(self, message_type: str) -> Any | None:
        value = self._messages.get(message_type)
        return None if value is None else value[0]

    def _mode(self) -> str:
        connection = self._require_connection()
        return str(connection.flightmode or "UNKNOWN").upper()

    def ensure_guided(self) -> None:
        mode = self._mode()
        if mode != "GUIDED":
            raise SafetyError(
                f"Vehicle must be in GUIDED mode; current mode is {mode}."
            )

    def ensure_land_available(self) -> None:
        if not self._is_armed():
            raise SafetyError("Cannot request LAND mode when the vehicle is disarmed.")
        modes = self._require_connection().mode_mapping()
        if not modes or "LAND" not in modes:
            raise SafetyError("The autopilot does not advertise a LAND mode.")

    def _is_armed(self) -> bool:
        heartbeat = self._latest("HEARTBEAT")
        if heartbeat is None:
            return False
        return bool(
            heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )

    def get_telemetry(self) -> dict[str, Any]:
        self._receive_for(0.25)
        position = self._latest("GLOBAL_POSITION_INT")
        hud = self._latest("VFR_HUD")
        attitude = self._latest("ATTITUDE")
        gps = self._latest("GPS_RAW_INT")
        status = self._latest("SYS_STATUS")
        local = self._latest("LOCAL_POSITION_NED")
        home = self._latest("HOME_POSITION")
        distance = self._latest("DISTANCE_SENSOR")
        ekf = self._latest("EKF_STATUS_REPORT")
        fields: dict[str, Any] = {
            "connected": True,
            "mode": self._mode(),
            "armed": self._is_armed(),
            "position": None,
            "heading_degrees": None,
            "groundspeed_m_s": None,
            "airspeed_m_s": None,
            "vertical_speed_m_s": None,
            "attitude_degrees": None,
            "gps": None,
            "battery": None,
            "local_position_ned_m": None,
            "message_age_seconds": {
                name: round(time.monotonic() - stamp, 2)
                for name, (_, stamp) in self._messages.items()
                if name in {
                    "GLOBAL_POSITION_INT",
                    "VFR_HUD",
                    "ATTITUDE",
                    "GPS_RAW_INT",
                    "SYS_STATUS",
                    "LOCAL_POSITION_NED",
                    "HOME_POSITION",
                    "DISTANCE_SENSOR",
                    "EKF_STATUS_REPORT",
                }
            },
        }
        if position is not None:
            fields["position"] = {
                "latitude_degrees": position.lat / 1e7,
                "longitude_degrees": position.lon / 1e7,
                "altitude_m_amsl": position.alt / 1000,
                "altitude_m_relative": position.relative_alt / 1000,
            }
            fields["heading_degrees"] = (
                None if position.hdg == 65535 else position.hdg / 100
            )
            fields["vertical_speed_m_s"] = -position.vz / 100
        if hud is not None:
            fields["groundspeed_m_s"] = hud.groundspeed
            fields["airspeed_m_s"] = hud.airspeed
            fields["vertical_speed_m_s"] = hud.climb
            if fields["heading_degrees"] is None:
                fields["heading_degrees"] = hud.heading
        if attitude is not None:
            fields["attitude_degrees"] = {
                "roll": math.degrees(attitude.roll),
                "pitch": math.degrees(attitude.pitch),
                "yaw": math.degrees(attitude.yaw),
            }
        if gps is not None:
            fields["gps"] = {
                "fix_type": gps.fix_type,
                "satellites_visible": gps.satellites_visible,
                "horizontal_accuracy_m": gps.eph / 100 if gps.eph != 65535 else None,
                "vertical_accuracy_m": gps.epv / 100 if gps.epv != 65535 else None,
            }
        if status is not None:
            fields["battery"] = {
                "voltage_v": (
                    None
                    if status.voltage_battery == 65535
                    else status.voltage_battery / 1000
                ),
                "remaining_percent": (
                    None
                    if status.battery_remaining < 0
                    else status.battery_remaining
                ),
                "current_a": (
                    None
                    if status.current_battery < 0
                    else status.current_battery / 100
                ),
            }
        if local is not None:
            fields["local_position_ned_m"] = {
                "north": local.x,
                "east": local.y,
                "down": local.z,
            }
        if home is not None:
            fields["home_position"] = {
                "latitude_degrees": home.latitude / 1e7,
                "longitude_degrees": home.longitude / 1e7,
                "altitude_m_amsl": home.altitude / 1000,
            }
        if distance is not None:
            fields["distance_sensor"] = {
                "current_distance_m": distance.current_distance / 100,
                "minimum_distance_m": distance.min_distance / 100,
                "maximum_distance_m": distance.max_distance / 100,
            }
        if ekf is not None:
            fields["ekf_flags"] = ekf.flags
        return fields

    def _send_command(self, command: int, *params: float) -> None:
        connection = self._require_connection()
        if len(params) != 7:
            raise ValueError("MAVLink command requires exactly seven parameters")
        connection.mav.command_long_send(
            connection.target_system,
            connection.target_component,
            command,
            0,
            *params,
        )
        self._wait_command_ack(command)

    def _wait_command_ack(self, command: int) -> None:
        connection = self._require_connection()
        deadline = time.monotonic() + self.settings.command_timeout_s
        while time.monotonic() < deadline:
            message = connection.recv_match(
                type=["COMMAND_ACK", "HEARTBEAT"],
                blocking=True,
                timeout=max(0.01, deadline - time.monotonic()),
            )
            if message is None:
                continue
            self._remember(message)
            if (
                message.get_type() == "COMMAND_ACK"
                and message.command == command
            ):
                if message.result != mavutil.mavlink.MAV_RESULT_ACCEPTED:
                    raise SafetyError(
                        f"Autopilot rejected MAV_CMD {command} "
                        f"with result {message.result}."
                    )
                return
        raise TimeoutError(f"No acknowledgement received for MAV_CMD {command}")

    def _wait_armed(self, expected: bool) -> None:
        connection = self._require_connection()
        deadline = time.monotonic() + self.settings.command_timeout_s
        while time.monotonic() < deadline:
            heartbeat = connection.recv_match(
                type="HEARTBEAT",
                blocking=True,
                timeout=max(0.01, deadline - time.monotonic()),
            )
            if heartbeat is not None:
                self._remember(heartbeat)
                if self._is_armed() == expected:
                    return
        raise TimeoutError(
            "Vehicle did not report the expected armed state before timeout"
        )

    def takeoff(self, altitude_m: float) -> dict[str, Any]:
        self.ensure_guided()
        if not self._is_armed():
            self._send_command(
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
            )
            self._wait_armed(True)
        self._send_command(
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0,
            0,
            0,
            float("nan"),
            0,
            0,
            altitude_m,
        )
        return {
            "status": "takeoff command accepted",
            "target_altitude_m": altitude_m,
            "note": "Check telemetry and maintain direct visual supervision.",
        }

    def move_relative(
        self, forward_back: float, left_right: float, up_down: float
    ) -> dict[str, Any]:
        self.ensure_guided()
        connection = self._require_connection()
        type_mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )
        connection.mav.set_position_target_local_ned_send(
            0,
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,
            type_mask,
            forward_back,
            left_right,
            -up_down,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        return {
            "status": "relative position target sent",
            "forward_back_m": forward_back,
            "left_right_m": left_right,
            "up_down_m": up_down,
            "note": "Target delivery is not proof of arrival; verify telemetry.",
        }

    def yaw_rotate(self, degrees: float, direction: str) -> dict[str, Any]:
        self.ensure_guided()
        clockwise = 1 if direction == "right" else -1
        self._send_command(
            mavutil.mavlink.MAV_CMD_CONDITION_YAW,
            degrees,
            20,
            clockwise,
            1,
            0,
            0,
            0,
        )
        return {
            "status": "yaw command accepted",
            "degrees": degrees,
            "direction": direction,
        }

    def land(self) -> dict[str, Any]:
        self.ensure_land_available()
        connection = self._require_connection()
        connection.set_mode("LAND")
        deadline = time.monotonic() + self.settings.command_timeout_s
        while time.monotonic() < deadline:
            heartbeat = connection.recv_match(
                type="HEARTBEAT",
                blocking=True,
                timeout=max(0.01, deadline - time.monotonic()),
            )
            if heartbeat is not None:
                self._remember(heartbeat)
                if self._mode() == "LAND":
                    return {"status": "LAND mode confirmed"}
        raise TimeoutError("Autopilot did not confirm LAND mode before timeout")
