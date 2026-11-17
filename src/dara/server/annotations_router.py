"""Text labels the analyst puts on the fit plot of a task.

They belong to the task record (like the review in ``review_router``), so every fit and version
of the task shows the same labels. The text is untrusted plain text: the client escapes it before
drawing, and the server only bounds it.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from dara.server import quick_refine
from dara.server.utils import get_worker_store

router = APIRouter(prefix="/api")

MAX_ANNOTATIONS = 50
MAX_TEXT = 120


class PlotAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
    two_theta: float = Field(ge=0, le=180)
    intensity: float = Field(ge=-1e12, le=1e12)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]

    @field_validator("two_theta", "intensity")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value

    @field_validator("text")
    @classmethod
    def _plain_line(cls, value: str) -> str:
        # One line of plain text: no control characters (newlines, tabs, escapes).
        if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
            raise ValueError("must be one line of plain text")
        return value


class AnnotationsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotations: list[PlotAnnotation] = Field(max_length=MAX_ANNOTATIONS)

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [item.id for item in self.annotations]
        if len(set(ids)) != len(ids):
            raise ValueError("annotation ids must be unique")
        return self


def _stored(task: dict) -> list[dict]:
    """Annotations of a task record; anything malformed in the record is left out."""
    valid = []
    for item in (task.get("annotations") or [])[:MAX_ANNOTATIONS]:
        try:
            valid.append(PlotAnnotation.model_validate(item).model_dump())
        except Exception:  # noqa: BLE001 - a damaged entry must not hide the others
            continue
    return valid


@router.get("/task/{task_id}/annotations")
async def get_annotations(task_id: int):
    return {"annotations": _stored(quick_refine.completed_task(task_id))}


@router.put("/task/{task_id}/annotations")
async def save_annotations(task_id: int, update: AnnotationsUpdate):
    """Replace the task's annotations with ``update.annotations`` (the full list)."""
    task = quick_refine.completed_task(task_id)
    annotations = [item.model_dump() for item in update.annotations]
    # Reload immediately before writing, preserving the rest of the task record.
    with get_worker_store() as store:
        current = store.query_one({"index": task_id})
        if current is None or current.get("uuid") != task.get("uuid"):
            raise HTTPException(404, "Task no longer exists")
        current["annotations"] = annotations
        current["annotations_updated_at"] = datetime.now(timezone.utc).isoformat()
        store.update(current)
    return {"annotations": annotations}
