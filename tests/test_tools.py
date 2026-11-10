import unittest
from dataclasses import replace
from unittest.mock import Mock

from mavlink_llm_pilot.config import Settings
from mavlink_llm_pilot.safety import SafetyError, SafetyPolicy
from mavlink_llm_pilot.tools import DroneTools


def test_settings() -> Settings:
    return Settings(
        cohere_api_key="test-key",
        model="command-a-plus-05-2026",
        mavlink_connection="udpin:127.0.0.1:14550",
        live_commands=True,
        max_altitude_m=5,
        max_move_m=3,
        max_vertical_move_m=2,
        max_yaw_degrees=45,
        heartbeat_timeout_s=1,
        command_timeout_s=1,
    )


class DroneToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = Mock()
        settings = test_settings()
        self.policy = SafetyPolicy(settings)
        self.tools = DroneTools(self.controller, settings, self.policy)

    def test_move_rejects_vector_longer_than_total_limit(self) -> None:
        with self.assertRaisesRegex(SafetyError, "total move distance"):
            self.tools.execute(
                "move_relative",
                {"forward_back": 2.5, "left_right": 2.5, "up_down": 0},
            )
        self.controller.move_relative.assert_not_called()

    def test_move_rejects_boolean_as_number(self) -> None:
        with self.assertRaisesRegex(ValueError, "forward_back must be a number"):
            self.tools.execute(
                "move_relative",
                {"forward_back": True, "left_right": 0, "up_down": 0},
            )
        self.controller.move_relative.assert_not_called()

    def test_takeoff_rejects_out_of_range_altitude(self) -> None:
        with self.assertRaisesRegex(SafetyError, "alt_meters"):
            self.tools.execute("takeoff", {"alt_meters": 6})
        self.controller.takeoff.assert_not_called()

    def test_yaw_rejects_invalid_direction(self) -> None:
        with self.assertRaisesRegex(ValueError, "direction"):
            self.tools.execute("yaw_rotate", {"degrees": 30, "direction": "north"})
        self.controller.yaw_rotate.assert_not_called()

    def test_telemetry_is_read_only(self) -> None:
        expected = {"connected": True}
        self.controller.get_telemetry.return_value = expected
        self.assertEqual(self.tools.execute("get_telemetry", {}), expected)

    def test_unknown_argument_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly these arguments"):
            self.tools.execute("land", {"confirm": True})
        self.controller.land.assert_not_called()

    def test_live_command_requires_explicit_enablement(self) -> None:
        disabled = replace(test_settings(), live_commands=False)
        with self.assertRaisesRegex(SafetyError, "disabled"):
            SafetyPolicy(disabled).authorize("land", {})


if __name__ == "__main__":
    unittest.main()
