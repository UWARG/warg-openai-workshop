from dataclasses import dataclass
import math
import os


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    cohere_api_key: str
    model: str
    mavlink_connection: str
    live_commands: bool
    max_altitude_m: float
    max_move_m: float
    max_vertical_move_m: float
    max_yaw_degrees: float
    heartbeat_timeout_s: float
    command_timeout_s: float
    tool_call_delay_s: float = 2
    provider: str = "cohere"
    openai_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("LLM_PROVIDER", "cohere").strip().lower()
        if provider not in {"cohere", "openai"}:
            raise ValueError("LLM_PROVIDER must be 'cohere' or 'openai'")
        model = os.getenv(
            "MODEL",
            os.getenv(
                "COHERE_MODEL" if provider == "cohere" else "OPENAI_MODEL",
                "command-a-plus-05-2026" if provider == "cohere" else "gpt-6-luna",
            ),
        )
        settings = cls(
            cohere_api_key=os.getenv("COHERE_API_KEY", ""),
            model=model,
            mavlink_connection=os.getenv(
                "MAVLINK_CONNECTION", "udpin:127.0.0.1:14550"
            ),
            live_commands=_boolean("LIVE_COMMANDS", False),
            max_altitude_m=_positive_float("MAX_ALTITUDE_M", 5),
            max_move_m=_positive_float("MAX_MOVE_M", 3),
            max_vertical_move_m=_positive_float("MAX_VERTICAL_MOVE_M", 2),
            max_yaw_degrees=_positive_float("MAX_YAW_DEGREES", 45),
            heartbeat_timeout_s=_positive_float("HEARTBEAT_TIMEOUT_S", 10),
            command_timeout_s=_positive_float("COMMAND_TIMEOUT_S", 5),
            tool_call_delay_s=_positive_float("TOOL_CALL_DELAY_S", 2),
            provider=provider,
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        )
        if settings.provider == "cohere" and not settings.cohere_api_key:
            raise ValueError("COHERE_API_KEY is required when LLM_PROVIDER=cohere")
        if settings.provider == "openai" and not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if settings.max_altitude_m > 30:
            raise ValueError("MAX_ALTITUDE_M cannot exceed the hard limit of 30 m")
        if settings.max_move_m > 10:
            raise ValueError("MAX_MOVE_M cannot exceed the hard limit of 10 m")
        if settings.max_vertical_move_m > 5:
            raise ValueError(
                "MAX_VERTICAL_MOVE_M cannot exceed the hard limit of 5 m"
            )
        if settings.max_yaw_degrees > 180:
            raise ValueError("MAX_YAW_DEGREES cannot exceed the hard limit of 180")
        if settings.tool_call_delay_s < 1:
            raise ValueError("TOOL_CALL_DELAY_S cannot be less than 1 second")
        if settings.tool_call_delay_s > 30:
            raise ValueError("TOOL_CALL_DELAY_S cannot exceed the hard limit of 30 seconds")
        return settings
