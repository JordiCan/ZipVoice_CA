from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SampleItem(BaseModel):
    id: str
    label: str
    text: str
    prompt_text: str
    prompt_audio_s3_key: Optional[str] = None
    prompt_audio_url: Optional[str] = None
    prompt_audio_name: Optional[str] = None
    cached_result_s3_key: Optional[str] = None
    cached_result_url: Optional[str] = None


class CreateJobRequest(BaseModel):
    sample_id: str = Field(..., min_length=1)


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    sample_id: str
    status: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    worker_id: Optional[str] = None
    result_s3_key: Optional[str] = None
    result_url: Optional[str] = None
    error: Optional[str] = None
    sample: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingJobResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    job: Optional[JobResponse] = None


class WorkerResultRequest(BaseModel):
    worker_id: str = Field(..., min_length=1)
    status: str = Field(..., pattern="^(completed|failed)$")
    result_s3_key: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
