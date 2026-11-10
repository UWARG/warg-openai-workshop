# MAVLink LLM Pilot

A Python CLI that uses Cohere tool calling to read telemetry and issue a small,
bounded set of MAVLink commands. It expects a human pilot to stay in control.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer.
2. Copy `.env.example` to `.env`, add your Cohere API key, and keep
   `LIVE_COMMANDS=false` while setting up.
3. Configure Mission Planner to forward MAVLink to `127.0.0.1:14550`.
   Change `MAVLINK_CONNECTION` if you use a different local endpoint.
4. Install the project and run it:

   ```powershell
   uv sync
   uv run mavlink-llm-pilot
   ```

The app waits for an autopilot heartbeat before opening the chat. The default
MAVLink endpoint is a loopback-only UDP listener; do not expose it to an
untrusted network. Telemetry and prompts are sent to Cohere for tool use.
Tools requested together are executed sequentially with a minimum
`TOOL_CALL_DELAY_S` pause between calls (default 2 seconds; allowed range
1–30 seconds). Calls that depend on a prior action's outcome or updated
telemetry must happen in separate chat turns; the delay does not mean a
maneuver has completed.

## Tools

- `get_telemetry()`: flight mode, armed state, GPS position/quality, altitude,
  heading, speed, attitude, local position, and battery, when available.
- `takeoff(alt_meters)`: arms if necessary, then requests GUIDED takeoff.
- `move_relative(forward_back, left_right, up_down)`: sends one body-relative
  position target in meters. Positive lateral is right; positive vertical is up.
- `yaw_rotate(degrees, direction)`: relative yaw rotation to the left or right.
- `land()`: asks the autopilot to enter LAND mode; it does not confirm touchdown.

## Flight safety

Flight commands are disabled by default. Enabling `LIVE_COMMANDS=true` requires
typing `YES` separately for every action in the interactive terminal. Commands
are bounded by `MAX_ALTITUDE_M`, `MAX_MOVE_M`, `MAX_VERTICAL_MOVE_M`, and
`MAX_YAW_DEGREES`; hard ceilings are 30 m, 10 m, 5 m, and 180 degrees,
respectively. The vehicle must report GUIDED mode for takeoff, movement, and
yaw. LAND is allowed from any armed mode.

These are not a geofence or a substitute for configured autopilot failsafes.
Validate the connection, vehicle behavior, and limits in SITL before flight.
Use a safe test area, maintain visual line of sight, and keep Mission Planner
available for immediate manual control. A MAVLink command being accepted does
not prove the aircraft reached its target. The LLM must not be used as the
sole flight-safety mechanism.

## Development

Run the unit tests with:

```powershell
uv run python -m unittest discover -s tests
```
