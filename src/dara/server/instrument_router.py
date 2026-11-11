import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
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


def _store(label, geometry, radiation, notes, verified, verified_by, files, extra=None):
    identity = str(uuid4())
    metadata = {**(extra or {}), "id": f"user-{identity}", "label": label.strip(), "geometry": geometry.strip(),
                "radiation": radiation.strip(), "notes": notes, "verified": verified,
                "verified_by": verified_by if verified else "", "verification": "operator-declared" if verified else "unverified",
                "created_at": datetime.now(timezone.utc).isoformat(), "has_ger": ".ger" in files, "has_sav": ".sav" in files,
                "sha256": {key: hashlib.sha256(value).hexdigest() for key, value in files.items() if key != ".sav"}}
    directory = registry_directory() / identity
    directory.mkdir(parents=True)
    for extension, content in files.items():  # .sav is stored but not hashed: it is not used in fits
        if extension == ".sav":
            (directory / "instrument.sav").write_text(content, encoding="utf-8")
            continue
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
    files = {".geq": geq, **({".ger": ger} if ger else {}), ".sav": device}
    return _store(setup.label, geometry, setup.radiation, setup.notes, False, "", files)


# Calibration jobs live in memory like quick-refine jobs; a server restart forgets them.
_CALIBRATIONS = {}
_CALIBRATION_LOCK = threading.Lock()


@router.post("/instrument-profiles/calibrate")
async def calibrate_profile(
    profile: str = Form(..., min_length=1, max_length=200),
    standard: Literal["Si", "LaB6", "Al2O3"] = Form(...),
    wavelength: Literal["Cu", "Co", "Cr", "Fe", "Mo"] = Form("Cu"),
    pattern_file: UploadFile = File(...),
):
    """Start fitting DetW of a profile to a measured standard; poll the returned job."""
    from dara.xrd import load_pattern

    try:
        device = instrument_profiles.device_file_text(profile)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Instrument profile not found.") from error
    if device is None:
        raise HTTPException(400, "This profile has no device file (.sav); calibration needs one.")
    if instrument_profiles.device_detw(device) is None:
        raise HTTPException(400, "DetW in this device file is not a plain number; calibration needs one.")
    folder = Path(tempfile.mkdtemp(prefix="dara-standard-"))
    pattern_path = folder / Path(pattern_file.filename or "standard.xy").name
    pattern_path.write_bytes(await pattern_file.read(64 * 1024 * 1024))
    try:
        load_pattern(pattern_path)
    except Exception as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"Could not read pattern: {error}") from error
    base = instrument_profiles.registered_profiles().get(profile) or {
        "label": profile, "geometry": f"BGMN template {profile}", "radiation": wavelength}
    job_id = uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "completed": 0, "total": instrument_profiles.SCAN_COMPILES,
           "profile": profile, "standard": standard, "wavelength": wavelength}
    with _CALIBRATION_LOCK:  # one at a time: each compile already uses all cores
        if any(other["status"] == "running" for other in _CALIBRATIONS.values()):
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(409, "Another calibration is running; wait for it to finish.")
        _CALIBRATIONS[job_id] = job
    threading.Thread(target=_run_calibration, args=(job, device, pattern_path, base), daemon=True,
                     name=f"dara-calibrate-{job_id}").start()
    return dict(job)


def _run_calibration(job, device, pattern_path, base):
    def progress(done, total):
        job.update(completed=done, total=total)

    try:
        fit = instrument_profiles.calibrate_detw(device, pattern_path, job["standard"], job["wavelength"], progress)
        day = datetime.now(timezone.utc).date().isoformat()
        suffix = f" · calibrated ({job['standard']}, {day})"
        calibration = {"standard": job["standard"], "pattern": pattern_path.name, "source_profile": job["profile"],
                       "detw": round(fit["detw"], 5), "detw_start": fit["detw_start"], "rwp_start": fit["rwp_start"],
                       "rwp": fit["rwp"], "apparent_size_nm": fit["apparent_size_nm"],
                       "curve": [[round(d, 5), r] for d, r in fit["curve"]], "date": day}
        files = {".geq": fit["geq"], **({".ger": fit["ger"]} if fit["ger"] else {}), ".sav": fit["sav"]}
        notes = (f"DetW fitted to {job['standard']} ({pattern_path.name}): {fit['detw_start']:g} -> {fit['detw']:.4g} mm, "
                 f"Rwp {fit['rwp_start']:.2f} -> {fit['rwp']:.2f} %"
                 + (f", apparent size {fit['apparent_size_nm']:.0f} nm." if fit["apparent_size_nm"] else "."))
        label = base["label"].split(" · calibrated (")[0]  # recalibration replaces the old suffix
        stored = _store(label[:120 - len(suffix)] + suffix, base["geometry"], base["radiation"], notes,
                        False, "", files, {"calibration": calibration})
        job.update(status="done", result=stored)
    except Exception as error:  # noqa: BLE001 - reported to the user through the job
        job.update(status="failed", error=str(error)[-500:])
    finally:
        shutil.rmtree(pattern_path.parent, ignore_errors=True)


@router.get("/instrument-profiles/calibrate/{job_id}")
async def calibration_status(job_id: str):
    job = _CALIBRATIONS.get(job_id)
    if job is None:
        raise HTTPException(404, "Calibration job not found (the server may have restarted).")
    return dict(job)
