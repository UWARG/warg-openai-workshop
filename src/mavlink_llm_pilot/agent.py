import json
import time
from typing import Any

import cohere

from .tools import DroneTools

SYSTEM_PROMPT = """You are an assistant for a human-operated drone. Use drone tools
for every request that needs drone data or an action; never claim an action ran
without a tool result. The aircraft is expected to be in GUIDED mode for flight
commands. Never infer telemetry, location, arrival, or safety from a command
being accepted. Explain tool failures clearly. Tool calls in one response run
sequentially with a delay, but do not batch actions that depend on each other's
results or on updated telemetry; make those requests in separate turns. Keep all
motion conservative. The operator remains responsible for visual line of sight,
legal operation, failsafes, and immediate control."""

MAX_TOOL_ROUNDS = 12

class CohereDroneAgent:
    def __init__(self, api_key: str, model: str, tools: DroneTools) -> None:
        self.client = cohere.ClientV2(api_key)
        self.model = model
        self.tools = tools
        self.tool_call_delay_s = tools.settings.tool_call_delay_s
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
            for index, call in enumerate(calls):
                if index > 0:
                    time.sleep(self.tool_call_delay_s)
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                    result = {
                        "result": self.tools.execute(call.function.name, arguments)
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
