import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import cohere
from openai import OpenAI

from .tools import DroneTools

SYSTEM_PROMPT = """You are an assistant for a human-operated drone. Use drone tools
for every request that needs drone data or an action; never claim an action ran
without a tool result. The aircraft is expected to be in GUIDED mode for flight
commands. Never infer telemetry, location, arrival, or safety from a command
being accepted. Explain tool failures clearly. Tool calls in one response run
sequentially, and the application—not you—adds the configured delay between
executions. Do not wait, insert get_telemetry, or ask for another turn between
independent actions in a requested sequence. Emit all independent sequence
actions as tool calls in the requested order in the same response. Use
get_telemetry when the user asks for it or when a safety decision genuinely
requires current telemetry, not merely to pause between movement legs. Keep all
motion conservative. The operator remains responsible for visual line of sight,
legal operation, failsafes, and immediate control."""

MAX_TOOL_ROUNDS = 12


@dataclass(frozen=True)
class ToolApproval:
    approved: bool
    skip_delay: bool = False


class CohereDroneAgent:
    def __init__(
        self,
        api_key: str,
        model: str,
        tools: DroneTools,
        tool_batch_observer: Callable[
            [list[tuple[str, dict[str, Any]]]], bool | ToolApproval
        ]
        | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.client = cohere.ClientV2(api_key)
        self.model = model
        self.tools = tools
        self.tool_call_delay_s = tools.settings.tool_call_delay_s
        self.tool_batch_observer = tool_batch_observer
        self.tool_executor = tool_executor or tools.execute
        self._last_tool_execution_at: float | None = None
        self.messages: list[Any] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def respond(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.chat(
                model=self.model,
                messages=self.messages,
                tools=self.tools.definitions,
            )
            message = response.message
            calls = message.tool_calls or []
            if not calls:
                text = "\n".join(
                    block.text
                    for block in (message.content or [])
                    if getattr(block, "text", None)
                )
                self.messages.append(message)
                return text or "(The model returned no text.)"

            self.messages.append(message)
            parsed_calls: list[tuple[Any, dict[str, Any]]] = []
            for call in calls:
                try:
                    parsed_calls.append(
                        (call, json.loads(call.function.arguments or "{}"))
                    )
                except (ValueError, TypeError) as error:
                    parsed_calls.append((call, {"_parse_error": str(error)}))

            for index, (call, arguments) in enumerate(parsed_calls):
                try:
                    approved = True
                    skip_delay = False
                    if self.tool_batch_observer is not None:
                        approval = self.tool_batch_observer(
                            [(call.function.name, arguments)]
                        )
                        if isinstance(approval, ToolApproval):
                            approved = approval.approved
                            skip_delay = approval.skip_delay
                        else:
                            approved = approval
                    if not approved:
                        result = {"error": "Operator denied this tool call."}
                    elif "_parse_error" in arguments:
                        result = {"error": arguments["_parse_error"]}
                    else:
                        if skip_delay:
                            self._last_tool_execution_at = time.monotonic()
                        else:
                            self._wait_between_tools()
                        result = {
                            "result": self.tool_executor(call.function.name, arguments)
                        }
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    RuntimeError,
                    TimeoutError,
                ) as error:
                    result = {"error": str(error)}
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": [
                            {
                                "type": "document",
                                "document": {"data": json.dumps(result)},
                            }
                        ],
                    }
                )
        raise RuntimeError("Cohere exceeded the maximum tool-use rounds")

    def _wait_between_tools(self) -> None:
        if self._last_tool_execution_at is not None:
            elapsed = time.monotonic() - self._last_tool_execution_at
            remaining = self.tool_call_delay_s - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_tool_execution_at = time.monotonic()


class OpenAIDroneAgent:
    def __init__(
        self,
        api_key: str,
        model: str,
        tools: DroneTools,
        tool_batch_observer: Callable[
            [list[tuple[str, dict[str, Any]]]], bool | ToolApproval
        ]
        | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.tools = tools
        self.tool_call_delay_s = tools.settings.tool_call_delay_s
        self.tool_batch_observer = tool_batch_observer
        self.tool_executor = tool_executor or tools.execute
        self._last_tool_execution_at: float | None = None
        self.input_items: list[Any] = []

    def respond(self, user_text: str) -> str:
        self.input_items.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.responses.create(
                model=self.model,
                instructions=SYSTEM_PROMPT,
                tools=self._tool_definitions(),
                input=self.input_items,
            )
            self.input_items.extend(response.output)
            calls = [
                item for item in response.output if item.type == "function_call"
            ]
            if not calls:
                return response.output_text or "(The model returned no text.)"

            for call in calls:
                result = self._execute_call(
                    call.name,
                    call.arguments,
                )
                self.input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )
        raise RuntimeError("OpenAI exceeded the maximum tool-use rounds")

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": definition["function"]["name"],
                "description": definition["function"]["description"],
                "parameters": definition["function"]["parameters"],
            }
            for definition in self.tools.definitions
        ]

    def _execute_call(self, name: str, raw_arguments: str) -> dict[str, Any]:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except (ValueError, TypeError) as error:
            return {"error": str(error)}
        try:
            approved = True
            skip_delay = False
            if self.tool_batch_observer is not None:
                approval = self.tool_batch_observer([(name, arguments)])
                if isinstance(approval, ToolApproval):
                    approved = approval.approved
                    skip_delay = approval.skip_delay
                else:
                    approved = approval
            if not approved:
                return {"error": "Operator denied this tool call."}
            if skip_delay:
                self._last_tool_execution_at = time.monotonic()
            else:
                self._wait_between_tools()
            return {"result": self.tool_executor(name, arguments)}
        except (OSError, ValueError, TypeError, RuntimeError, TimeoutError) as error:
            return {"error": str(error)}

    def _wait_between_tools(self) -> None:
        if self._last_tool_execution_at is not None:
            elapsed = time.monotonic() - self._last_tool_execution_at
            remaining = self.tool_call_delay_s - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_tool_execution_at = time.monotonic()


def create_agent(
    provider: str,
    api_key: str,
    model: str,
    tools: DroneTools,
    tool_batch_observer: Callable[
        [list[tuple[str, dict[str, Any]]]], bool | ToolApproval
    ]
    | None = None,
    tool_executor: Callable[[str, dict[str, Any]], Any] | None = None,
) -> CohereDroneAgent | OpenAIDroneAgent:
    agent_type = CohereDroneAgent if provider == "cohere" else OpenAIDroneAgent
    return agent_type(
        api_key,
        model,
        tools,
        tool_batch_observer=tool_batch_observer,
        tool_executor=tool_executor,
    )
