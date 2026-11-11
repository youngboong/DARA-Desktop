import hashlib
import json
import subprocess
from typing import Literal
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from dara import instrument_profiles
from dara.instrument_profiles import registry_directory

router = APIRouter(prefix="/api")


@router.post("/instrument-profiles")
async def register_profile(
    label: str = Form(..., min_length=1, max_length=120),
    geometry: str = Form(..., min_length=1, max_length=250),
    radiation: str = Form(..., min_length=1, max_length=60),
    notes: str = Form("", max_length=2000),
    verified: bool = Form(False),
    verified_by: str = Form("", max_length=120),
    geq: UploadFile = File(...),
    ger: UploadFile | None = File(None),
):
    if not label.strip() or not geometry.strip() or not radiation.strip():
        raise HTTPException(400, "Label, measurement geometry and radiation are required.")
    if verified and (not verified_by.strip() or not notes.strip()):
        raise HTTPException(400, "Record the verifier and calibration/standard evidence before marking a profile verified.")
    files = {}
    for upload, extension in ((geq, ".geq"), (ger, ".ger")):
        if upload is None:
            continue
        if not (upload.filename or "").lower().endswith(extension):
            raise HTTPException(400, f"Expected a {extension} file")
        content = await upload.read(8 * 1024 * 1024 + 1)
        if not content or len(content) > 8 * 1024 * 1024:
            raise HTTPException(400, "Profile files must be nonempty and at most 8 MB each.")
        files[extension] = content
    return _store(label, geometry, radiation, notes, verified, verified_by, files)


def _store(label, geometry, radiation, notes, verified, verified_by, files):
    identity = str(uuid4())
    metadata = {"id": f"user-{identity}", "label": label.strip(), "geometry": geometry.strip(),
                "radiation": radiation.strip(), "notes": notes, "verified": verified,
                "verified_by": verified_by if verified else "", "verification": "operator-declared" if verified else "unverified",
                "created_at": datetime.now(timezone.utc).isoformat(), "has_ger": ".ger" in files,
                "sha256": {key: hashlib.sha256(value).hexdigest() for key, value in files.items()}}
    directory = registry_directory() / identity
    directory.mkdir(parents=True)
    for extension, content in files.items():
        (directory / f"instrument{extension}").write_bytes(content)
    # Publish metadata last. Existing registrations are never overwritten.
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


class InstrumentSetup(BaseModel):
    """Bragg-Brentano instrument in datasheet units (mm, degrees)."""

    label: str = Field(..., min_length=1, max_length=120)
    radiation: str = Field(..., min_length=1, max_length=60)
    notes: str = Field("", max_length=2000)
    radius: float = Field(..., gt=50, le=1000)
    focus_length: float = Field(12, gt=0, le=50)
    focus_width: float = Field(0.04, gt=0, le=2)
    divergence: Literal["fixed", "automatic"] = "fixed"
    divergence_angle: float | None = Field(None, gt=0, le=10)
    irradiated_length: float | None = Field(None, gt=0, le=60)
    divergence_distance: float = Field(..., gt=0, le=1000)
    soller_primary: float = Field(2.5, gt=0, le=20)
    soller_secondary: float = Field(2.5, gt=0, le=20)
    bruker_soller: bool = False
    sample_diameter: float = Field(25, gt=0, le=100)
    beam_mask: float | None = Field(None, gt=0, le=50)
    beam_mask_distance: float | None = Field(None, gt=0, le=1000)
    antiscatter_width: float | None = Field(None, gt=0, le=50)
    antiscatter_distance: float | None = Field(None, gt=0, le=1000)
    detector: Literal["linear", "point"] = "linear"
    detector_length: float | None = Field(None, gt=0, le=100)
    strip_width: float = Field(..., gt=0, le=5)
    detector_height: float | None = Field(None, gt=0, le=100)
    monochromator: Literal["none", "graphite"] = "none"


@router.post("/instrument-profiles/build")
async def build_profile(setup: InstrumentSetup):
    """Register a profile that BGMN (GEOMET, MAKEGEQ) computes from the instrument settings."""
    if not setup.label.strip() or not setup.radiation.strip():
        raise HTTPException(400, "Profile name and radiation are required.")
    if setup.divergence_distance >= setup.radius:
        raise HTTPException(400, "The divergence slit must sit between the tube and the sample (distance < radius).")
    if setup.divergence == "fixed" and not setup.divergence_angle:
        raise HTTPException(400, "Enter the divergence slit angle.")
    if setup.divergence == "automatic" and not setup.irradiated_length:
        raise HTTPException(400, "Enter the irradiated length of the automatic slit.")
    if setup.detector == "linear" and not setup.detector_length:
        raise HTTPException(400, "Enter the length of the strip detector.")
    device = instrument_profiles.setup_device_file(setup.model_dump())
    try:
        geq, ger = await run_in_threadpool(instrument_profiles.compile_device_file, device)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(500, f"Could not compile the profile: {error}") from error
    slit = (f"fixed {setup.divergence_angle:g}°" if setup.divergence == "fixed"
            else f"automatic {setup.irradiated_length:g} mm")
    geometry = (f"Bragg-Brentano R {setup.radius:g} mm, divergence {slit}, Soller {setup.soller_primary:g}/"
                f"{setup.soller_secondary:g}°, {setup.detector} detector"
                + (", graphite monochromator" if setup.monochromator == "graphite" else ""))
    files = {".geq": geq, **({".ger": ger} if ger else {})}
    stored = _store(setup.label, geometry, setup.radiation, setup.notes, False, "", files)
    (registry_directory() / stored["id"].removeprefix("user-") / "instrument.sav").write_text(device, encoding="utf-8")
    return stored
