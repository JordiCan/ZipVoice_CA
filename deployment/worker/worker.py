from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from deployment.api.runtime import (
    ensure_model_artifacts,
    load_inference_pipeline,
    upload_s3_file,
)
from zipvoice.bin.infer_zipvoice import generate_sentence

LOGGER = logging.getLogger(__name__)

API_URL = os.environ.get("EC2_API_URL", "http://127.0.0.1:8000").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "").strip()
WORKER_ID = os.environ.get("WORKER_ID", "local-worker")
MODEL_DIR = Path(os.environ.get("ZIPVOICE_MODEL_DIR", "models/zipvoice_ca_runtime"))
RESULTS_PREFIX = os.environ.get("ZIPVOICE_S3_RESULTS_PREFIX", "zipvoice-ca/results").strip("/")
POLL_INTERVAL = float(os.environ.get("ZIPVOICE_JOB_POLL_INTERVAL_SECONDS", "5"))
DEFAULT_INFERENCE = {
    "model_name": "zipvoice",
    "checkpoint_name": "zipvoice_ca.pt",
    "tokenizer_name": "espeak",
    "lang": "ca",
    "guidance_scale": 1.0,
    "num_step": 25,
}


def build_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Worker-Token": WORKER_TOKEN,
        "X-Worker-Id": WORKER_ID,
    }


def request_json(method: str, path: str, payload: Optional[dict] = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=body,
        headers=build_headers(),
        method=method,
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def download_file(url: str, suffix: str) -> Path:
    with urllib.request.urlopen(url, timeout=300) as response:
        data = response.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp") as tmp_file:
        tmp_file.write(data)
        return Path(tmp_file.name)


def safe_unlink(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("Could not delete temporary file %s", path, exc_info=True)


def build_runtime() -> dict:
    model_dir = ensure_model_artifacts(MODEL_DIR)
    pipeline = load_inference_pipeline(
        model_name=DEFAULT_INFERENCE["model_name"],
        model_dir=str(model_dir),
        checkpoint_name=DEFAULT_INFERENCE["checkpoint_name"],
        tokenizer_name=DEFAULT_INFERENCE["tokenizer_name"],
        lang=DEFAULT_INFERENCE["lang"],
    )
    pipeline["model_dir"] = str(model_dir)
    return pipeline


def process_job(runtime: dict, job: dict) -> None:
    prompt_url = job.get("prompt_audio_url")
    if not prompt_url:
        raise RuntimeError("Job is missing prompt_audio_url.")

    prompt_path = None
    output_path = None
    try:
        prompt_suffix = Path(job.get("prompt_audio_name") or "prompt.wav").suffix
        prompt_path = download_file(prompt_url, suffix=prompt_suffix or ".wav")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir="/tmp") as tmp_out:
            output_path = Path(tmp_out.name)

        generate_sentence(
            save_path=str(output_path),
            prompt_text=job["prompt_text"],
            prompt_wav=str(prompt_path),
            text=job["target_text"],
            model=runtime["model"],
            vocoder=runtime["vocoder"],
            tokenizer=runtime["tokenizer"],
            feature_extractor=runtime["feature_extractor"],
            device=runtime["device"],
            num_step=DEFAULT_INFERENCE["num_step"],
            guidance_scale=DEFAULT_INFERENCE["guidance_scale"],
            sampling_rate=runtime["sampling_rate"],
        )

        result_stem = job.get("source_sample_id") or job.get("input_origin") or "prompt"
        result_key = f"{RESULTS_PREFIX}/{result_stem}/{job['id']}.wav"
        if not upload_s3_file(file_path=output_path, key=result_key):
            raise RuntimeError("Failed to upload synthesized audio to S3.")

        request_json(
            "POST",
            f"/jobs/{job['id']}/result",
            {
                "worker_id": WORKER_ID,
                "status": "completed",
                "result_s3_key": result_key,
                "metadata": {
                    "model_dir": runtime["model_dir"],
                    "sampling_rate": runtime["sampling_rate"],
                },
            },
        )
    except Exception as exc:
        LOGGER.exception("Job %s failed", job["id"])
        request_json(
            "POST",
            f"/jobs/{job['id']}/result",
            {
                "worker_id": WORKER_ID,
                "status": "failed",
                "error": str(exc),
                "metadata": {},
            },
        )
    finally:
        safe_unlink(prompt_path)
        safe_unlink(output_path)


def run() -> None:
    if not WORKER_TOKEN:
        raise RuntimeError("WORKER_TOKEN must be configured.")

    runtime = build_runtime()
    LOGGER.info("Worker %s ready and polling %s", WORKER_ID, API_URL)

    while True:
        try:
            pending = request_json("GET", "/jobs/pending")
            job = pending.get("job")
            if not job:
                time.sleep(POLL_INTERVAL)
                continue
            process_job(runtime, job)
        except urllib.error.HTTPError as exc:
            LOGGER.error("Worker request failed with HTTP %s", exc.code)
            time.sleep(POLL_INTERVAL)
        except urllib.error.URLError:
            LOGGER.exception("Worker could not reach the API")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("ZIPVOICE_WORKER_LOG_LEVEL", "INFO"))
    run()
