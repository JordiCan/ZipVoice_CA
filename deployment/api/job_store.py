from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Optional
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso8601(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class Job:
    id: str
    sample_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    owner_token: str
    sample: dict
    worker_id: Optional[str] = None
    result_s3_key: Optional[str] = None
    result_url: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["created_at"] = to_iso8601(self.created_at)
        payload["updated_at"] = to_iso8601(self.updated_at)
        payload["started_at"] = to_iso8601(self.started_at)
        payload["completed_at"] = to_iso8601(self.completed_at)
        return payload


class InMemoryJobStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()

    def create_job(
        self,
        *,
        sample: dict,
        owner_token: str,
        cached_result: Optional[dict] = None,
    ) -> Job:
        now = utc_now()
        job_id = uuid4().hex
        if cached_result is not None:
            job = Job(
                id=job_id,
                sample_id=sample["id"],
                status="served_from_cache",
                created_at=now,
                updated_at=now,
                owner_token=owner_token,
                sample=sample,
                worker_id="cache",
                result_s3_key=cached_result.get("result_s3_key"),
                result_url=cached_result.get("result_url"),
                completed_at=now,
                metadata={"cache": True, **cached_result.get("metadata", {})},
            )
        else:
            job = Job(
                id=job_id,
                sample_id=sample["id"],
                status="pending",
                created_at=now,
                updated_at=now,
                owner_token=owner_token,
                sample=sample,
            )

        with self._lock:
            self._jobs[job_id] = job
            if cached_result is None:
                self._queue.append(job_id)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def claim_next_job(self, *, worker_id: str) -> Optional[Job]:
        with self._lock:
            while self._queue:
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.status != "pending":
                    continue
                now = utc_now()
                job.status = "processing"
                job.worker_id = worker_id
                job.started_at = now
                job.updated_at = now
                return job
        return None

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        result_s3_key: Optional[str],
        result_url: Optional[str],
        error: Optional[str],
        metadata: Optional[dict],
    ) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            now = utc_now()
            job.status = status
            job.worker_id = worker_id
            job.result_s3_key = result_s3_key
            job.result_url = result_url
            job.error = error
            job.metadata = metadata or {}
            job.updated_at = now
            job.completed_at = now if status in {"completed", "failed"} else None
            return job
