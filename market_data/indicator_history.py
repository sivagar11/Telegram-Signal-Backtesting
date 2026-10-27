"""Resumable historical bid/ask downloads for signal-time indicators."""
import json
import os
import hashlib
import tempfile
from pathlib import Path

import pandas as pd

from market_data.oanda_client import OandaClient, GRANULARITY_SECONDS
from market_data.history_validation import audit_history, validate_candles

HISTORY_DIR = Path("data/prices/indicators")
# H1 needs >1,000 closed bars before the first signal for EMA warm-up.
WARMUP_DAYS = {"H1": 90, "M15": 14, "M5": 7}


def download_history(signals, force=False):
    """Cache each <=4,999-interval page, including exact request provenance."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    client = OandaClient()
    histories, manifest = {}, []
    try:
        for granularity, days in WARMUP_DAYS.items():
            duration = pd.Timedelta(seconds=GRANULARITY_SECONDS[granularity])
            start = (signals.timestamp_utc.min() - pd.Timedelta(days=days)).floor(duration)
            end = signals.timestamp_utc.max().ceil(duration)
            instrument = os.getenv("OANDA_INSTRUMENT", "XAU_USD")
            environment = os.getenv("OANDA_ENV", "practice").lower()
            frames, cursor = [], start
            while cursor < end:
                stop = min(cursor + duration * 4999, end)
                stem = f"{instrument}_{environment}_BA_{granularity}_{cursor:%Y%m%dT%H%M%S}_{stop:%Y%m%dT%H%M%S}"
                path = HISTORY_DIR / f"{stem}.csv"
                meta_path = path.with_suffix(".json")
                metadata = dict(instrument=instrument, environment=environment,
                                price="BA", granularity=granularity,
                                start=cursor.isoformat(), end=stop.isoformat())
                cached = False
                if not force and path.exists() and meta_path.exists():
                    try:
                        cached = json.loads(meta_path.read_text()) == metadata
                    except (ValueError, OSError):
                        pass
                if cached:
                    frame = pd.read_csv(path)
                    frame["time"] = pd.to_datetime(frame["time"], utc=True)
                else:
                    print(f"Downloading {granularity}: {cursor} -> {stop}", flush=True)
                    frame = client.get_candles(cursor, stop, granularity)
                    frame.to_csv(path, index=False)
                    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
                frames.append(frame)
                manifest.append({**metadata, "candles": len(frame), "file": str(path)})
                cursor = stop
            history = pd.concat(frames, ignore_index=True)
            if history.empty:
                raise ValueError(f"No {granularity} history returned")
            history = history.drop_duplicates("time").sort_values("time").reset_index(drop=True)
            histories[granularity] = history
            print(f"{granularity}: {len(history):,} candles ready", flush=True)
    finally:
        client.close()
        if manifest:
            pd.DataFrame(manifest).to_csv(HISTORY_DIR / "download_manifest.csv", index=False)
    return histories


def atomic_write(path, content):
    """Same-directory replace; abandoned temp files can never be cache entries."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".partial-", delete=False) as file:
            name = file.name
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write_verified_page(path, frame, request, provenance):
    """Metadata commits the exact CSV hash, bounds and row count as one generation."""
    payload = frame.to_csv(index=False).encode()
    metadata = dict(schema_version=2, request=request, rows=len(frame),
                    sha256=hashlib.sha256(payload).hexdigest(), provenance=provenance)
    atomic_write(path, payload)
    atomic_write(path.with_suffix(".json"), (json.dumps(metadata, indent=2) + "\n").encode())
    return metadata


def read_verified_page(path, request):
    """A stale metadata/CSV pair, truncated file or edited cache is rejected."""
    metadata = json.loads(path.with_suffix(".json").read_text())
    payload = path.read_bytes()
    if (not isinstance(metadata, dict) or metadata.get("schema_version") != 2 or metadata.get("request") != request
            or metadata.get("sha256") != hashlib.sha256(payload).hexdigest()):
        raise ValueError("Cache generation/checksum mismatch")
    if not isinstance(metadata.get("provenance"), dict) or "source" not in metadata["provenance"]:
        raise ValueError("Cache provenance missing")
    frame = pd.read_csv(path)
    if len(frame) != metadata.get("rows"):
        raise ValueError("Cache row count mismatch")
    frame = validate_candles(frame, request["start"], request["end"], request["granularity"])
    return frame, metadata


def download_validated_history(signals, report_dir, force=False):
    """Experiment 1.1: adopt validated legacy pages without modifying their files."""
    instrument = os.getenv("OANDA_INSTRUMENT", "XAU_USD")
    if instrument != "XAU_USD":
        raise ValueError("The session calendar is explicitly for XAU_USD")
    environment = os.getenv("OANDA_ENV", "practice").lower()
    directory = HISTORY_DIR / "validated_v2"
    directory.mkdir(parents=True, exist_ok=True)
    histories, manifest, gap_frames, stats = {}, [], [], []
    client = None
    try:
        for tf, days in WARMUP_DAYS.items():
            duration = pd.Timedelta(seconds=GRANULARITY_SECONDS[tf])
            start = (signals.timestamp_utc.min() - pd.Timedelta(days=days)).floor(duration)
            end = signals.timestamp_utc.max().ceil(duration)
            frames, cursor = [], start
            while cursor < end:
                stop = min(cursor + duration * 4999, end)
                name = f"{instrument}_{environment}_BA_{tf}_{cursor:%Y%m%dT%H%M%S}_{stop:%Y%m%dT%H%M%S}.csv"
                path, legacy = directory / name, HISTORY_DIR / name
                request = dict(instrument=instrument, environment=environment, price="BA",
                               granularity=tf, start=cursor.isoformat(), end=stop.isoformat())
                frame = None
                if not force:
                    try:
                        frame, metadata = read_verified_page(path, request)
                    except (ValueError, OSError, KeyError, TypeError):
                        # Legacy files have no integrity proof; validate them before adoption.
                        try:
                            if json.loads(legacy.with_suffix(".json").read_text()) != request:
                                raise ValueError("Legacy request mismatch")
                            frame = validate_candles(pd.read_csv(legacy), cursor, stop, tf)
                            metadata = write_verified_page(path, frame, request, {
                                "source": "validated_legacy_adoption",
                                "legacy_sha256": hashlib.sha256(legacy.read_bytes()).hexdigest()})
                        except (ValueError, OSError, KeyError, TypeError):
                            frame = None
                if frame is None:
                    print(f"Downloading validated {tf}: {cursor} -> {stop}", flush=True)
                    if client is None:
                        client = OandaClient()
                    frame = validate_candles(client.get_candles(cursor, stop, tf), cursor, stop, tf)
                    metadata = write_verified_page(path, frame, request, {"source": "oanda_api"})
                frames.append(frame)
                manifest.append({**request, "candles": len(frame), "sha256": metadata["sha256"],
                                 "file": str(path), "source": metadata["provenance"]["source"]})
                cursor = stop
            history, gaps, stat = audit_history(pd.concat(frames, ignore_index=True), start, end, tf)
            histories[tf] = history
            gap_frames.append(gaps)
            stats.append(stat)
            print(f"{tf}: {len(history):,} validated candles; {stat['unexpected_missing_candles']} unexplained missing slots", flush=True)
    finally:
        if client:
            client.close()
        if manifest:
            pd.DataFrame(manifest).to_csv(report_dir / "history_manifest.csv", index=False)
    all_gaps = pd.concat(gap_frames, ignore_index=True)
    all_gaps.to_csv(report_dir / "history_gaps.csv", index=False)
    pd.DataFrame(stats).to_csv(report_dir / "history_quality.csv", index=False)
    return histories, all_gaps, pd.DataFrame(stats)
