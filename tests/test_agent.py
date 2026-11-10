import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from mavlink_llm_pilot.agent import CohereDroneAgent
from mavlink_llm_pilot.config import Settings
from mavlink_llm_pilot.safety import SafetyPolicy
from mavlink_llm_pilot.tools import DroneTools


class AgentTests(unittest.TestCase):
    def test_turn_runs_multiple_tools_in_order_with_delay(self) -> None:
        settings = Settings(
            cohere_api_key="test-key",
            model="command-a-plus-05-2026",
            mavlink_connection="udpin:127.0.0.1:14550",
            live_commands=False,
            max_altitude_m=5,
            max_move_m=3,
            max_vertical_move_m=2,
            max_yaw_degrees=45,
            heartbeat_timeout_s=1,
            command_timeout_s=1,
        )
        controller = Mock()
        controller.get_telemetry.return_value = {"armed": False}
        tools = DroneTools(controller, settings, SafetyPolicy(settings))
        agent = CohereDroneAgent("test-key", settings.model, tools)
        agent.client = Mock()
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="get_telemetry", arguments="{}"),
        )
        second_tool_call = SimpleNamespace(
            id="call-2",
            function=SimpleNamespace(name="get_telemetry", arguments="{}"),
        )
        agent.client.chat.side_effect = [
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[tool_call, second_tool_call],
                    content=None,
                )
            ),
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=None,
                    content=[SimpleNamespace(text="The vehicle is disarmed.")],
                )
            ),
        ]

        with patch("mavlink_llm_pilot.agent.time.sleep") as sleep:
            self.assertEqual(
                agent.respond("Check the drone."),
                "The vehicle is disarmed.",
            )
        sleep.assert_called_once_with(2)
        self.assertEqual(agent.client.chat.call_args_list[0].kwargs["tool_choice"], "REQUIRED")
        self.assertEqual(agent.client.chat.call_args_list[1].kwargs["tool_choice"], "NONE")
        self.assertEqual(controller.get_telemetry.call_count, 2)
        tool_messages = [
            item for item in agent.messages if isinstance(item, dict) and item.get("role") == "tool"
        ]
        self.assertEqual(
            [item["tool_call_id"] for item in tool_messages],
            ["call-1", "call-2"],
        )


if __name__ == "__main__":
    unittest.main()
