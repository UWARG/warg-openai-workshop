import sys
from typing import Any, Callable

from .config import Settings


class SafetyError(RuntimeError):
    """Raised when an operation fails a flight-safety check."""


class SafetyPolicy:
    def __init__(
        self,
        settings: Settings,
        confirmation: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.confirmation = confirmation

    def authorize(self, action: str, arguments: dict[str, Any]) -> None:
        if not self.settings.live_commands:
            raise SafetyError(
                "Live flight commands are disabled. Set LIVE_COMMANDS=true "
                "only after verifying the vehicle, area, and failsafes."
            )
        if self.confirmation is not None:
            if not self.confirmation(action, arguments):
                raise SafetyError("Operator did not confirm the command.")
            return
        if not sys.stdin.isatty():
            raise SafetyError(
                "Live commands require an interactive terminal for confirmation."
            )
        print(f"\nLIVE MAVLink action: {action}({arguments})")
        print("Confirm only if the aircraft and flight area are safe.")
        if input('Type "YES" to execute: ').strip() != "YES":
            raise SafetyError("Operator did not confirm the command.")

    def require_bound(self, name: str, value: float, limit: float) -> None:
        if abs(value) > limit:
            raise SafetyError(f"{name} must be between {-limit:g} and {limit:g}.")
