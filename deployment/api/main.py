import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import torchaudio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from deployment.api.runtime import (
    ensure_model_artifacts,
    list_s3_example_audio,
    load_inference_pipeline,
    load_sample_texts,
)
from zipvoice.bin.infer_zipvoice import generate_sentence

RUNTIME_MODEL_DIR = Path(
    os.environ.get("ZIPVOICE_MODEL_DIR", "models/zipvoice_ca_runtime")
)
MAX_TEXT_CHARS = int(os.environ.get("ZIPVOICE_MAX_TEXT_CHARS", "300"))
MAX_PROMPT_TEXT_CHARS = int(os.environ.get("ZIPVOICE_MAX_PROMPT_TEXT_CHARS", "300"))
MAX_PROMPT_AUDIO_BYTES = int(
    os.environ.get("ZIPVOICE_MAX_PROMPT_AUDIO_BYTES", str(10 * 1024 * 1024))
)
MAX_PROMPT_AUDIO_SECONDS = float(
    os.environ.get("ZIPVOICE_MAX_PROMPT_AUDIO_SECONDS", "30")
)
DEFAULT_INFERENCE = {
    "model_name": "zipvoice",
    "checkpoint_name": "zipvoice_ca.pt",
    "tokenizer_name": "espeak",
    "lang": "ca",
    "guidance_scale": 1.0,
    "num_step": 25,
}
ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "audio/x-m4a",
    "audio/mp4",
}
ALLOWED_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4"}

logger = logging.getLogger(__name__)
inference_lock = threading.Lock()


def safe_unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete temporary file %s", path, exc_info=True)


def validate_text_inputs(text: str, prompt_text: str) -> tuple[str, str]:
    text = text.strip()
    prompt_text = prompt_text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty.")
    if not prompt_text:
        raise HTTPException(status_code=400, detail="prompt_text must not be empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"text must be at most {MAX_TEXT_CHARS} characters.",
        )
    if len(prompt_text) > MAX_PROMPT_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"prompt_text must be at most {MAX_PROMPT_TEXT_CHARS} characters."
            ),
        )

    return text, prompt_text


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
        return ".m4a"
    raise HTTPException(
        status_code=400,
        detail="prompt_audio must be a supported audio file (wav, mp3, flac, ogg, m4a).",
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


def validate_audio_file(path: str) -> None:
    try:
        waveform, sample_rate = torchaudio.load(path)
    except Exception as exc:  # pragma: no cover - backend-specific failures
        raise HTTPException(
            status_code=400,
            detail="prompt_audio could not be decoded as a valid audio file.",
        ) from exc

    duration_seconds = waveform.shape[-1] / float(sample_rate)
    if duration_seconds > MAX_PROMPT_AUDIO_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                "prompt_audio is too long. "
                f"Maximum duration is {MAX_PROMPT_AUDIO_SECONDS:g} seconds."
            ),
        )


def build_runtime() -> dict:
    model_dir = ensure_model_artifacts(RUNTIME_MODEL_DIR)
    pipeline = load_inference_pipeline(
        model_name=DEFAULT_INFERENCE["model_name"],
        model_dir=str(model_dir),
        checkpoint_name=DEFAULT_INFERENCE["checkpoint_name"],
        tokenizer_name=DEFAULT_INFERENCE["tokenizer_name"],
        lang=DEFAULT_INFERENCE["lang"],
    )
    pipeline["model_dir"] = str(model_dir)
    pipeline["artifact_source"] = (
        "s3" if os.environ.get("ZIPVOICE_S3_BUCKET") else "huggingface"
    )
    pipeline["sample_texts"] = load_sample_texts(model_dir)
    pipeline["sample_audio"] = list_s3_example_audio()
    return pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = build_runtime()
    yield


app = FastAPI(
    title="ZipVoice-CA API",
    description="Demo API for Catalan zero-shot text-to-speech with ZipVoice-CA.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    runtime = app.state.runtime
    return {
        "status": "ok",
        "model_loaded": True,
        "artifact_source": runtime["artifact_source"],
        "device": str(runtime["device"]),
        "sampling_rate": runtime["sampling_rate"],
        "model_dir": runtime["model_dir"],
    }


@app.get("/examples")
def examples():
    runtime = app.state.runtime
    return {
        "sample_texts": runtime["sample_texts"],
        "sample_audio": runtime["sample_audio"],
    }


@app.post("/synthesize", response_class=FileResponse)
def synthesize(
    text: str = Form(...),
    prompt_text: str = Form(...),
    prompt_audio: UploadFile = File(...),
):
    text, prompt_text = validate_text_inputs(text, prompt_text)

    suffix = validate_upload_metadata(prompt_audio)
    prompt_path = persist_upload(prompt_audio, suffix=suffix)
    output_path = None

    try:
        validate_audio_file(prompt_path)
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav", dir="/tmp"
        ) as tmp_out:
            output_path = tmp_out.name

        runtime = app.state.runtime
        with inference_lock:
            generate_sentence(
                save_path=output_path,
                prompt_text=prompt_text,
                prompt_wav=prompt_path,
                text=text,
                model=runtime["model"],
                vocoder=runtime["vocoder"],
                tokenizer=runtime["tokenizer"],
                feature_extractor=runtime["feature_extractor"],
                device=runtime["device"],
                num_step=DEFAULT_INFERENCE["num_step"],
                guidance_scale=DEFAULT_INFERENCE["guidance_scale"],
                sampling_rate=runtime["sampling_rate"],
            )
    except HTTPException:
        if output_path is not None:
            safe_unlink(output_path)
        raise
    except Exception as exc:  # pragma: no cover - runtime depends on heavy ML stack
        if output_path is not None:
            safe_unlink(output_path)
        logger.exception("Synthesis failed")
        raise HTTPException(
            status_code=500, detail="Failed to synthesize audio."
        ) from exc
    finally:
        safe_unlink(prompt_path)
        prompt_audio.file.close()

    return FileResponse(
        path=output_path,
        media_type="audio/wav",
        filename="zipvoice_ca_output.wav",
        background=BackgroundTask(safe_unlink, output_path),
    )
