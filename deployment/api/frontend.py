from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


def resolve_frontend_index(dist_dir: Path) -> Path:
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "Frontend bundle is not available. Build the React app before serving the API."
            ),
        )
    return index_path


def serve_frontend(dist_dir: Path) -> FileResponse:
    return FileResponse(resolve_frontend_index(dist_dir))
