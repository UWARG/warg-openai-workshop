import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import threading
import uuid
from typing import Any

from cohere.core.api_error import ApiError
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import CohereDroneAgent
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
    action_version: int = 0


@dataclass
class WebState:
    settings: Settings
    controller: MavlinkController
    tools: DroneTools
    agent: CohereDroneAgent
    lock: threading.RLock
    jobs: dict[str, ChatJob] = field(default_factory=dict)
    current_job: ChatJob | None = None
    auto_approve: bool = False

    def confirm(self, action: str, arguments: dict[str, Any]) -> bool:
        job = self.current_job
        if job is None:
            return False
        if self.auto_approve:
            return True
        if job.action != action or job.arguments != arguments:
            self.observe(action, arguments)
        if not self.auto_approve:
            job.status = "approval_required"
        job.event.wait()
        return job.approved

    def observe(self, action: str, arguments: dict[str, Any]) -> None:
        job = self.current_job
        if job is not None:
            job.action = action
            job.arguments = arguments
            job.action_version += 1

    def telemetry(self) -> dict[str, Any]:
        with self.lock:
            return self.controller.get_telemetry()

    def chat(self, job: ChatJob, message: str, auto_approve: bool) -> str:
        with self.lock:
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
        tool_observer=state.observe,
    )
    state.agent = CohereDroneAgent(
        settings.cohere_api_key,
        settings.model,
        state.tools,
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
            except ApiError as error:
                job.error = f"Cohere API error: {error}"
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
            action_version=job.action_version,
            error=job.error,
        )

    @app.post("/api/chat/{job_id}/approval")
    async def chat_approval(job_id: str, approved: bool) -> dict[str, str]:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Chat request not found")
        if job.status != "approval_required":
            raise HTTPException(status_code=409, detail="No approval is pending")
        job.approved = approved
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
