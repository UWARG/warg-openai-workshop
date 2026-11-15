import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from mavlink_llm_pilot.agent import CohereDroneAgent, OpenAIDroneAgent
from mavlink_llm_pilot.config import Settings
from mavlink_llm_pilot.safety import SafetyPolicy
from mavlink_llm_pilot.tools import DroneTools


class AgentTests(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
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

    def test_turn_runs_multiple_tools_in_order_with_delay(self) -> None:
        settings = self._settings()
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
        third_tool_call = SimpleNamespace(
            id="call-3",
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
                    tool_calls=[third_tool_call],
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
        self.assertEqual(sleep.call_count, 2)
        for sleep_call in sleep.call_args_list:
            self.assertAlmostEqual(sleep_call.args[0], 2, delta=0.1)
        requests = [item.kwargs for item in agent.client.chat.call_args_list]
        self.assertEqual(len(requests), 3)
        for request in requests:
            self.assertIn("tools", request)
            self.assertNotIn("tool_choice", request)
            self.assertEqual(request["model"], "command-a-plus-05-2026")
        self.assertEqual(controller.get_telemetry.call_count, 3)
        tool_messages = [
            item for item in agent.messages if isinstance(item, dict) and item.get("role") == "tool"
        ]
        self.assertEqual(
            [item["tool_call_id"] for item in tool_messages],
            ["call-1", "call-2", "call-3"],
        )

    def test_denied_tool_calls_execute_no_tools(self) -> None:
        settings = Settings(
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
        controller = Mock()
        tools = DroneTools(controller, settings, SafetyPolicy(settings))
        observed: list[list[tuple[str, dict[str, object]]]] = []
        agent = CohereDroneAgent(
            "test-key",
            settings.model,
            tools,
            tool_batch_observer=lambda calls: observed.append(calls) or False,
        )
        agent.client = Mock()
        agent.client.chat.side_effect = [
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="land", arguments="{}"
                            ),
                        ),
                        SimpleNamespace(
                            id="call-2",
                            function=SimpleNamespace(
                                name="get_telemetry", arguments="{}"
                            ),
                        ),
                    ],
                    content=None,
                )
            ),
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=None,
                    content=[SimpleNamespace(text="The action was denied.")],
                )
            ),
        ]

        self.assertEqual(agent.respond("Land now."), "The action was denied.")
        self.assertEqual(
            observed,
            [[("land", {})], [("get_telemetry", {})]],
        )
        controller.land.assert_not_called()
        controller.get_telemetry.assert_not_called()

    def test_openai_responses_tool_call_executes_and_returns_output(self) -> None:
        settings = self._settings()
        controller = Mock()
        controller.get_telemetry.return_value = {"armed": False}
        tools = DroneTools(controller, settings, SafetyPolicy(settings))
        agent = OpenAIDroneAgent("test-key", "gpt-6-luna", tools)
        agent.client = Mock()
        call_item = SimpleNamespace(
            type="function_call",
            call_id="call-1",
            name="get_telemetry",
            arguments="{}",
        )
        agent.client.responses.create.side_effect = [
            SimpleNamespace(output=[call_item]),
            SimpleNamespace(output=[], output_text="The vehicle is disarmed."),
        ]

        self.assertEqual(agent.respond("Check the drone."), "The vehicle is disarmed.")
        controller.get_telemetry.assert_called_once_with()
        request = agent.client.responses.create.call_args_list[0].kwargs
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["tools"][0]["type"], "function")
        self.assertEqual(request["tools"][0]["name"], "get_telemetry")
        self.assertEqual(
            agent.input_items[-1],
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"result": {"armed": false}}',
            },
        )


if __name__ == "__main__":
    unittest.main()
