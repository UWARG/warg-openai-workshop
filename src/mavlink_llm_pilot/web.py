import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import threading
import uuid
from typing import Any

from cohere.core.api_error import ApiError
from openai import APIError
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import CohereDroneAgent, OpenAIDroneAgent, ToolApproval, create_agent
from .config import Settings
from .mavlink import MavlinkController
from .safety import SafetyPolicy
from .tools import DroneTools

WEB_DIR = Path(__file__).parent / "web"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    confirm_live: bool = False


class ChatResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status: str
    response: str | None = None
    action: str | None = None
    arguments: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    action_version: int = 0
    error: str | None = None


@dataclass
class ChatJob:
    event: threading.Event
    status: str = "running"
    response: str | None = None
    action: str | None = None
    arguments: dict[str, Any] | None = None
    error: str | None = None
    approved: bool = False
    approval_granted: bool = False
    approval_denied: bool = False
    approval_generation: int = 0
    approval_decision: bool | None = None
    last_approval_generation: int | None = None
    last_approval_decision: bool | None = None
    action_version: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WebState:
    settings: Settings
    controller: MavlinkController
    tools: DroneTools
    agent: CohereDroneAgent | OpenAIDroneAgent
    lock: threading.RLock
    chat_lock: threading.Lock = field(default_factory=threading.Lock)
    jobs: dict[str, ChatJob] = field(default_factory=dict)
    current_job: ChatJob | None = None
    auto_approve: bool = False

    def confirm(self, action: str, arguments: dict[str, Any]) -> bool:
        job = self.current_job
        if job is None:
            return False
        if self.auto_approve:
            return True
        if job.approval_granted:
            job.approval_granted = False
            return True
        return False

    def observe(
        self, action: str, arguments: dict[str, Any], increment: bool = True
    ) -> None:
        job = self.current_job
        if job is not None:
            job.action = action
            job.arguments = arguments
            if increment:
                job.action_version += 1
                job.actions = [{"action": action, "arguments": arguments}]

    def observe_batch(
        self, calls: list[tuple[str, dict[str, Any]]]
    ) -> bool | ToolApproval:
        job = self.current_job
        if job is None:
            return False
        job.actions = [
            {"action": action, "arguments": arguments}
            for action, arguments in calls
        ]
        job.action = calls[0][0] if calls else None
        job.arguments = calls[0][1] if calls else None
        job.action_version += 1
        batch_id = job.action_version
        action, arguments = calls[0]
        has_flight_action = action != "get_telemetry"
        if not has_flight_action:
            job.approval_granted = False
            job.tool_events.extend(
                {
                    "batch_id": batch_id,
                    "action": action,
                    "arguments": arguments,
                    "needs_approval": False,
                }
                for action, arguments in calls
            )
            return ToolApproval(True)
        if self.auto_approve:
            job.tool_events.extend(
                {
                    "batch_id": batch_id,
                    "action": action,
                    "arguments": arguments,
                    "needs_approval": False,
                }
                for action, arguments in calls
            )
            return ToolApproval(True)
        if job.approval_denied:
            return ToolApproval(False)
        job.approval_generation += 1
        approval_generation = job.approval_generation
        job.approval_decision = None
        job.approval_granted = False
        job.approval_denied = False
        job.event.clear()
        job.status = "approval_required"
        job.tool_events.extend(
            {
                "batch_id": batch_id,
                "approval_generation": approval_generation,
                "action": action,
                "arguments": arguments,
                "needs_approval": True,
            }
            for action, arguments in calls
        )
        while (
            job.approval_generation == approval_generation
            and job.approval_decision is None
        ):
            job.event.wait()
        return ToolApproval(job.approval_decision is True, skip_delay=True)

    def telemetry(self) -> dict[str, Any]:
        with self.lock:
            return self.controller.get_telemetry()

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with self.lock:
            return self.tools.execute(name, arguments)

    def chat(self, job: ChatJob, message: str, auto_approve: bool) -> str:
        with self.chat_lock:
            self.current_job = job
            self.auto_approve = auto_approve
            try:
                return self.agent.respond(message)
            finally:
                self.current_job = None
                self.auto_approve = False


def create_app(settings: Settings) -> FastAPI:
    state = WebState(
        settings=settings,
        controller=MavlinkController(settings),
        tools=None,  # type: ignore[assignment]
        agent=None,  # type: ignore[assignment]
        lock=threading.RLock(),
    )
    state.tools = DroneTools(
        state.controller,
        settings,
        SafetyPolicy(settings, confirmation=state.confirm),
    )
    api_key = (
        settings.cohere_api_key
        if settings.provider == "cohere"
        else settings.openai_api_key
    )
    state.agent = create_agent(
        settings.provider,
        api_key,
        settings.model,
        state.tools,
        tool_batch_observer=state.observe_batch,
        tool_executor=state.execute_tool,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await asyncio.to_thread(state.controller.connect)
        try:
            yield
        finally:
            state.controller.close()

    app = FastAPI(title="MAVLink LLM Pilot", lifespan=lifespan)
    app.state.drone = state

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/assets/{filename}")
    async def asset(filename: str) -> FileResponse:
        if filename not in {"app.js", "styles.css"}:
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(WEB_DIR / filename)

    @app.get("/api/telemetry")
    async def telemetry() -> dict[str, Any]:
        try:
            return await asyncio.to_thread(state.telemetry)
        except (RuntimeError, OSError, TimeoutError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/config")
    async def runtime_config() -> dict[str, str]:
        return {"provider": settings.provider, "model": settings.model}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        job_id = uuid.uuid4().hex
        job = ChatJob(event=threading.Event())
        state.jobs[job_id] = job

        def run() -> None:
            try:
                job.response = state.chat(
                    job,
                    request.message.strip(),
                    request.confirm_live,
                )
                job.status = "complete"
            except (ApiError, APIError) as error:
                job.error = f"{settings.provider.title()} API error: {error}"
                job.status = "error"
            except (RuntimeError, OSError, TimeoutError, ValueError) as error:
                job.error = str(error)
                job.status = "error"
            except Exception as error:
                job.error = f"Unexpected server error: {error}"
                job.status = "error"

        threading.Thread(target=run, daemon=True).start()
        return ChatResponse(job_id=job_id)

    @app.get("/api/chat/{job_id}", response_model=JobStatus)
    async def chat_status(job_id: str) -> JobStatus:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Chat request not found")
        return JobStatus(
            status=job.status,
            response=job.response,
            action=job.action,
            arguments=job.arguments,
            actions=job.actions,
            tool_events=job.tool_events,
            action_version=job.action_version,
            error=job.error,
        )

    @app.post("/api/chat/{job_id}/approval")
    async def chat_approval(
        job_id: str, approved: bool, generation: int | None = None
    ) -> dict[str, str]:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Chat request not found")
        if (
            generation is not None
            and generation == job.last_approval_generation
            and approved == job.last_approval_decision
        ):
            return {"status": "accepted"}
        if job.approval_denied:
            return {"status": "already_denied"}
        if job.status != "approval_required":
            raise HTTPException(status_code=409, detail="No approval is pending")
        if generation is not None and generation != job.approval_generation:
            raise HTTPException(status_code=409, detail="Approval is no longer pending")
        job.approved = approved
        job.last_approval_generation = job.approval_generation
        job.last_approval_decision = approved
        job.approval_decision = approved
        job.approval_granted = approved
        job.approval_denied = not approved
        job.status = "running"
        job.event.set()
        return {"status": "accepted"}

    return app


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
