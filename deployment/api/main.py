from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from deployment.api.frontend import serve_frontend
from deployment.api.job_store import InMemoryJobStore, utc_now
from deployment.api.models import (
    CreateJobRequest,
    JobResponse,
    PendingJobResponse,
    WorkerResultRequest,
)
from deployment.api.runtime import (
    enrich_sample_urls,
    get_cached_result_for_sample,
    get_result_download_url,
    get_sample_by_id,
    load_cached_results_manifest,
    load_samples_manifest,
)

DEMO_MODE = os.environ.get("ZIPVOICE_DEMO_MODE", "hybrid")
WORKER_TOKEN = os.environ.get("ZIPVOICE_WORKER_TOKEN", "").strip()
FRONTEND_DIST_DIR = Path(
    os.environ.get("ZIPVOICE_FRONTEND_DIST_DIR", "deployment/frontend/dist")
)


def build_runtime() -> dict:
    samples = load_samples_manifest()
    cached_results = load_cached_results_manifest()
    samples_by_id = {sample["id"]: sample for sample in samples}
    return {
        "demo_mode": DEMO_MODE,
        "job_store": InMemoryJobStore(),
        "samples": samples,
        "samples_by_id": samples_by_id,
        "cached_results": cached_results,
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
    version="2.0.0",
    lifespan=lifespan,
)

assets_dir = FRONTEND_DIST_DIR / "assets"
app.mount(
    "/assets",
    StaticFiles(directory=str(assets_dir), check_dir=False),
    name="assets",
)


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


@app.post("/jobs", response_model=JobResponse, status_code=201)
def create_job(payload: CreateJobRequest):
    runtime = app.state.runtime
    sample = get_sample_by_id(runtime["samples_by_id"], payload.sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail="Unknown sample_id.")

    cached_result = get_cached_result_for_sample(
        cached_results=runtime["cached_results"],
        sample=sample,
    )
    job = runtime["job_store"].create_job(
        sample=sample,
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
    if full_path.startswith(("health", "samples", "jobs", "docs", "openapi.json")):
        raise HTTPException(status_code=404, detail="Not found.")

    dist_dir = app.state.runtime["frontend_dist_dir"]
    requested = dist_dir / full_path
    if requested.is_file():
        return FileResponse(requested)
    return serve_frontend(dist_dir)
