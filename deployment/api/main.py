from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from deployment.api.frontend import serve_frontend
from deployment.api.job_store import InMemoryJobStore, utc_now
from deployment.api.models import JobResponse, PendingJobResponse, ReferencePromptResponse, WorkerResultRequest
from deployment.api.runtime import (
    build_user_input_s3_key,
    enrich_sample_urls,
    get_cached_result_for_sample,
    get_result_download_url,
    get_s3_bucket,
    get_sample_by_id,
    load_cached_results_manifest,
    load_reference_prompts,
    load_samples_manifest,
    upload_s3_file,
)

DEMO_MODE = os.environ.get("ZIPVOICE_DEMO_MODE", "hybrid")
WORKER_TOKEN = os.environ.get("ZIPVOICE_WORKER_TOKEN", "").strip()
FRONTEND_DIST_DIR = Path(
    os.environ.get("ZIPVOICE_FRONTEND_DIST_DIR", "deployment/frontend/dist")
)
MAX_TEXT_CHARS = int(os.environ.get("ZIPVOICE_MAX_TEXT_CHARS", "300"))
MAX_PROMPT_TEXT_CHARS = int(os.environ.get("ZIPVOICE_MAX_PROMPT_TEXT_CHARS", "300"))
MAX_PROMPT_AUDIO_BYTES = int(
    os.environ.get("ZIPVOICE_MAX_PROMPT_AUDIO_BYTES", str(10 * 1024 * 1024))
)
MAX_RECORDED_AUDIO_SECONDS = float(
    os.environ.get("ZIPVOICE_MAX_RECORDED_AUDIO_SECONDS", "10")
)
ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "audio/webm",
    "audio/x-m4a",
    "audio/mp4",
}
ALLOWED_SUFFIXES = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".webm",
    ".m4a",
    ".mp4",
}


def build_runtime() -> dict:
    samples = load_samples_manifest()
    cached_results = load_cached_results_manifest()
    reference_prompts = load_reference_prompts()
    samples_by_id = {sample["id"]: sample for sample in samples}
    return {
        "demo_mode": DEMO_MODE,
        "job_store": InMemoryJobStore(),
        "samples": samples,
        "samples_by_id": samples_by_id,
        "cached_results": cached_results,
        "reference_prompts": reference_prompts,
        "frontend_dist_dir": FRONTEND_DIST_DIR,
        "worker_last_seen_at": None,
        "worker_last_seen_worker_id": None,
        "s3_enabled": bool(os.environ.get("ZIPVOICE_S3_BUCKET")),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = build_runtime()
    yield


app = FastAPI(
    title="ZipVoice-CA Hybrid API",
    description="Hybrid cloud-local demo API for Catalan zero-shot text-to-speech with ZipVoice-CA.",
    version="2.1.0",
    lifespan=lifespan,
)

assets_dir = FRONTEND_DIST_DIR / "assets"
app.mount(
    "/assets",
    StaticFiles(directory=str(assets_dir), check_dir=False),
    name="assets",
)


def safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def require_worker_token(x_worker_token: str = Header(default="")) -> str:
    if not WORKER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Worker support is disabled because ZIPVOICE_WORKER_TOKEN is not configured.",
        )
    if x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token.")
    return x_worker_token


def touch_worker(app_: FastAPI, worker_id: str) -> None:
    runtime = app_.state.runtime
    runtime["worker_last_seen_at"] = utc_now()
    runtime["worker_last_seen_worker_id"] = worker_id


def serialize_job(job) -> JobResponse:
    return JobResponse(**job.as_dict())


def validate_target_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise HTTPException(status_code=400, detail="text must not be empty.")
    if len(value) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"text must be at most {MAX_TEXT_CHARS} characters.",
        )
    return value


def validate_prompt_text(prompt_text: str) -> str:
    value = prompt_text.strip()
    if not value:
        raise HTTPException(status_code=400, detail="prompt_text must not be empty.")
    if len(value) > MAX_PROMPT_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"prompt_text must be at most {MAX_PROMPT_TEXT_CHARS} characters.",
        )
    return value


def validate_upload_metadata(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    content_type = (upload.content_type or "").lower()

    if suffix and suffix in ALLOWED_SUFFIXES:
        return suffix
    if content_type in ALLOWED_CONTENT_TYPES:
        if content_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
            return ".wav"
        if content_type in {"audio/mpeg", "audio/mp3"}:
            return ".mp3"
        if content_type in {"audio/flac", "audio/x-flac"}:
            return ".flac"
        if content_type == "audio/ogg":
            return ".ogg"
        if content_type == "audio/webm":
            return ".webm"
        return ".m4a"
    raise HTTPException(
        status_code=400,
        detail="prompt_audio must be a supported audio file.",
    )


def persist_upload(upload: UploadFile, suffix: str) -> str:
    bytes_written = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp") as tmp_file:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                return tmp_file.name
            bytes_written += len(chunk)
            if bytes_written > MAX_PROMPT_AUDIO_BYTES:
                tmp_path = tmp_file.name
                tmp_file.close()
                safe_unlink(tmp_path)
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "prompt_audio is too large. "
                        f"Maximum size is {MAX_PROMPT_AUDIO_BYTES} bytes."
                    ),
                )
            tmp_file.write(chunk)


def validate_audio_duration(path: str, *, max_seconds: float) -> None:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        duration_seconds = float(completed.stdout.strip())
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="prompt_audio could not be decoded as a valid audio file.",
        ) from exc

    if duration_seconds > max_seconds:
        raise HTTPException(
            status_code=400,
            detail=(
                "prompt_audio is too long. "
                f"Maximum duration is {max_seconds:g} seconds."
            ),
        )    try:
        waveform, sample_rate = torchaudio.load(path)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="prompt_audio could not be decoded as a valid audio file.",
        ) from exc

    duration_seconds = waveform.shape[-1] / float(sample_rate)
    if duration_seconds > max_seconds:
        raise HTTPException(
            status_code=400,
            detail=(
                "prompt_audio is too long. "
                f"Maximum duration is {max_seconds:g} seconds."
            ),
        )


@app.get("/health")
def health():
    runtime = app.state.runtime
    last_seen_at = runtime["worker_last_seen_at"]
    return {
        "status": "ok",
        "demo_mode": runtime["demo_mode"],
        "s3_enabled": runtime["s3_enabled"],
        "worker_configured": bool(WORKER_TOKEN),
        "worker_last_seen_at": last_seen_at.isoformat().replace("+00:00", "Z")
        if last_seen_at
        else None,
        "worker_last_seen_worker_id": runtime["worker_last_seen_worker_id"],
        "sample_count": len(runtime["samples"]),
    }


@app.get("/samples")
def samples():
    runtime = app.state.runtime
    return {"samples": [enrich_sample_urls(dict(sample)) for sample in runtime["samples"]]}


@app.get("/reference-prompts", response_model=ReferencePromptResponse)
def reference_prompts():
    prompts = app.state.runtime["reference_prompts"]
    default_prompt = prompts[0] if prompts else ""
    return ReferencePromptResponse(prompts=prompts, default_prompt=default_prompt)


@app.post("/jobs", response_model=JobResponse, status_code=201)
def create_job(
    text: str = Form(...),
    source_type: str = Form(...),
    sample_id: str | None = Form(default=None),
    prompt_text: str | None = Form(default=None),
    input_origin: str | None = Form(default=None),
    prompt_audio: UploadFile | None = File(default=None),
):
    runtime = app.state.runtime
    target_text = validate_target_text(text)

    if source_type not in {"sample", "recorded_audio"}:
        raise HTTPException(status_code=400, detail="Invalid source_type.")

    source_sample = None
    source_sample_id = None
    normalized_prompt_text = ""
    prompt_audio_s3_key = None
    prompt_audio_url = None
    prompt_audio_name = None
    cached_result = None

    if source_type == "sample":
        if prompt_audio is not None:
            raise HTTPException(
                status_code=400,
                detail="prompt_audio is not allowed when source_type=sample.",
            )
        if not sample_id:
            raise HTTPException(status_code=400, detail="sample_id is required.")
        sample = get_sample_by_id(runtime["samples_by_id"], sample_id)
        if sample is None:
            raise HTTPException(status_code=404, detail="Unknown sample_id.")

        source_sample = sample
        source_sample_id = sample["id"]
        normalized_prompt_text = validate_prompt_text(sample["reference_text"])
        prompt_audio_s3_key = sample.get("prompt_audio_s3_key")
        prompt_audio_url = sample.get("prompt_audio_url")
        prompt_audio_name = sample.get("prompt_audio_name")
        input_origin = "sample"
        cached_result = get_cached_result_for_sample(
            cached_results=runtime["cached_results"],
            sample=sample,
            target_text=target_text,
        )
    else:
        if prompt_audio is None:
            raise HTTPException(
                status_code=400,
                detail="prompt_audio is required when source_type=recorded_audio.",
            )
        if not get_s3_bucket():
            raise HTTPException(
                status_code=503,
                detail="S3 must be configured to accept recorded audio prompts.",
            )
        normalized_prompt_text = validate_prompt_text(prompt_text or "")
        suffix = validate_upload_metadata(prompt_audio)
        tmp_path = persist_upload(prompt_audio, suffix=suffix)
        try:
            validate_audio_duration(tmp_path, max_seconds=MAX_RECORDED_AUDIO_SECONDS)
            prompt_audio_name = prompt_audio.filename or f"recorded{suffix}"
            prompt_audio_s3_key = build_user_input_s3_key(prompt_audio_name)
            if not upload_s3_file(
                file_path=Path(tmp_path),
                key=prompt_audio_s3_key,
                content_type=(prompt_audio.content_type or "audio/webm"),
            ):
                raise HTTPException(
                    status_code=502,
                    detail="Failed to upload the recorded audio to S3.",
                )
            prompt_audio_url = get_result_download_url(prompt_audio_s3_key)
            input_origin = "recorded"
        finally:
            safe_unlink(tmp_path)
            prompt_audio.file.close()

    job = runtime["job_store"].create_job(
        source_type=source_type,
        target_text=target_text,
        prompt_text=normalized_prompt_text,
        prompt_audio_s3_key=prompt_audio_s3_key,
        prompt_audio_url=prompt_audio_url,
        prompt_audio_name=prompt_audio_name,
        source_sample_id=source_sample_id,
        input_origin=input_origin,
        source_sample=source_sample,
        owner_token="public",
        cached_result=cached_result,
    )
    return serialize_job(job)


@app.get("/jobs/pending", response_model=PendingJobResponse)
def get_pending_job(
    _: str = Depends(require_worker_token),
    x_worker_id: str = Header(default="worker"),
):
    runtime = app.state.runtime
    worker_id = x_worker_id or "worker"
    touch_worker(app, worker_id)
    job = runtime["job_store"].claim_next_job(worker_id=worker_id)
    if job is None:
        return PendingJobResponse(job=None)
    return PendingJobResponse(job=serialize_job(job))


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    runtime = app.state.runtime
    job = runtime["job_store"].get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.result_s3_key:
        job.result_url = get_result_download_url(job.result_s3_key)
    if job.prompt_audio_s3_key:
        job.prompt_audio_url = get_result_download_url(job.prompt_audio_s3_key)
    return serialize_job(job)


@app.post("/jobs/{job_id}/result", response_model=JobResponse)
def post_job_result(
    job_id: str,
    payload: WorkerResultRequest,
    _: str = Depends(require_worker_token),
):
    touch_worker(app, payload.worker_id)
    result_url = (
        get_result_download_url(payload.result_s3_key)
        if payload.result_s3_key
        else None
    )
    job = app.state.runtime["job_store"].complete_job(
        job_id=job_id,
        worker_id=payload.worker_id,
        status=payload.status,
        result_s3_key=payload.result_s3_key,
        result_url=result_url,
        error=payload.error,
        metadata=payload.metadata,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return serialize_job(job)


@app.get("/", include_in_schema=False)
def frontend_index():
    return serve_frontend(app.state.runtime["frontend_dist_dir"])


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_spa(full_path: str):
    if full_path.startswith(
        ("health", "samples", "reference-prompts", "jobs", "docs", "openapi.json")
    ):
        raise HTTPException(status_code=404, detail="Not found.")

    dist_dir = app.state.runtime["frontend_dist_dir"]
    requested = dist_dir / full_path
    if requested.is_file():
        return FileResponse(requested)
    return serve_frontend(dist_dir)
