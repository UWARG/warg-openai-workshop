import sys

from cohere.core.api_error import ApiError
from openai import APIError
from dotenv import load_dotenv

from .agent import create_agent
from .config import Settings
from .mavlink import MavlinkController
from .safety import SafetyPolicy
from .tools import DroneTools


def main() -> None:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    controller = MavlinkController(settings)
    try:
        controller.connect()
        api_key = (
            settings.cohere_api_key
            if settings.provider == "cohere"
            else settings.openai_api_key
        )
        agent = create_agent(
            settings.provider,
            api_key,
            settings.model,
            DroneTools(controller, settings, SafetyPolicy(settings)),
        )
        print(
            "MAVLink LLM Pilot ready. Type 'quit' to exit. "
            f"Live flight commands: {'ENABLED' if settings.live_commands else 'disabled'}."
        )
        while True:
            try:
                user_text = input("\nYou: ").strip()
            except EOFError:
                break
            if user_text.lower() in {"quit", "exit"}:
                break
            if not user_text:
                continue
            try:
                print(f"\nPilot: {agent.respond(user_text)}")
            except (ApiError, APIError) as error:
                print(f"{settings.provider.title()} API error: {error}", file=sys.stderr)
            except (RuntimeError, TimeoutError, OSError) as error:
                print(f"Operation failed: {error}", file=sys.stderr)
    except (OSError, TimeoutError) as error:
        print(f"MAVLink connection error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        controller.close()
