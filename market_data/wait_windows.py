"""Independent cached execution windows spanning current-candle confirmation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import os
import threading
import time

import pandas as pd

from market_data.indicator_history import read_verified_page, write_verified_page
from market_data.history_validation import validate_candles
from market_data.oanda_client import OandaClient

WINDOW_DIR = Path("data/prices/experiment_1_2")


def requests_for_signal(signal):
    timestamp = pd.Timestamp(signal.timestamp_utc)
    close = timestamp.floor("5min") + pd.Timedelta(minutes=5)
    return {
        "S5": (timestamp.floor("5s"), close + pd.Timedelta(minutes=3)),
        "M1": (timestamp.floor("min"), close + pd.Timedelta(hours=5, minutes=1)),
    }


def download_wait_windows(signals, report_dir, workers=4, force=False,
                          request_builder=None, window_dir=None, source_manifest=None):
    directory = Path(window_dir) if window_dir is not None else WINDOW_DIR
    builder = request_builder or requests_for_signal
    directory.mkdir(parents=True, exist_ok=True)
    sources = (source_manifest.set_index(["message_id", "granularity"])
               if source_manifest is not None else None)
    lock = threading.Lock()
    next_call = [0.0]

    def throttle():
        with lock:
            now = time.monotonic()
            time.sleep(max(0., next_call[0] - now))
            next_call[0] = time.monotonic() + .1

    def job(signal):
        client = None
        entries = []
        try:
            for tf, (start, end) in builder(signal).items():
                instrument = os.getenv("OANDA_INSTRUMENT", "XAU_USD")
                environment = os.getenv("OANDA_ENV", "practice").lower()
                request = dict(instrument=instrument, environment=environment, price="BA", granularity=tf,
                               start=start.isoformat(), end=end.isoformat())
                path = directory / f"{instrument}_{environment}_{int(signal.message_id)}_{tf}.csv"
                cached = False
                if not force:
                    try:
                        frame, meta = read_verified_page(path, request)
                        cached = True
                    except (ValueError, OSError, KeyError, TypeError):
                        pass
                if not cached:
                    prefix, provenance, tail_start = None, {"source": "oanda_api"}, start
                    if not force and sources is not None and (int(signal.message_id), tf) in sources.index:
                        source = sources.loc[(int(signal.message_id), tf)]
                        prior_request = {key: tf if key == "granularity" else source[key] for key in request}
                        try:
                            if (prior_request["instrument"] != instrument or prior_request["environment"] != environment
                                    or pd.Timestamp(prior_request["start"]) != start):
                                raise ValueError("Prefix request differs")
                            prefix, prior_meta = read_verified_page(Path(source["file"]), prior_request)
                            tail_start = min(end, pd.Timestamp(prior_request["end"]))
                            prefix = prefix[prefix.time < end]
                            provenance = {"source": "verified_prefix_and_oanda_tail", "prefix_file": source["file"],
                                          "prefix_sha256": prior_meta["sha256"], "tail_start": tail_start.isoformat()}
                        except (ValueError, OSError, KeyError, TypeError):
                            prefix, tail_start = None, start
                    if tail_start < end:
                        if client is None:
                            client = OandaClient()
                        throttle()
                        tail = client.get_candles(tail_start, end, tf)
                        frame = pd.concat([prefix, tail], ignore_index=True) if prefix is not None else tail
                    else:
                        frame = prefix
                    frame = validate_candles(frame, start, end, tf)
                    meta = write_verified_page(path, frame, request, provenance)
                entries.append({"message_id": int(signal.message_id), **request, "candles": len(frame),
                                "sha256": meta["sha256"], "file": str(path)})
            return entries
        finally:
            if client:
                client.close()

    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(job, signal): int(signal.message_id) for _, signal in signals.iterrows()}
        for count, task in enumerate(as_completed(tasks), 1):
            try:
                rows.extend(task.result())
            except Exception as exc:
                errors.append(dict(message_id=tasks[task], error=str(exc)))
            if count % 25 == 0 or count == len(tasks):
                print(f"Execution windows: {count}/{len(tasks)} signals; errors={len(errors)}", flush=True)
                pd.DataFrame(rows).to_csv(report_dir / "execution_manifest.csv", index=False)
    if errors:
        pd.DataFrame(errors).to_csv(report_dir / "download_errors.csv", index=False)
        raise RuntimeError(f"{len(errors)} signals failed to download; see download_errors.csv and rerun to resume")
    manifest = pd.DataFrame(rows).sort_values(["message_id", "granularity"])
    manifest.to_csv(report_dir / "execution_manifest.csv", index=False)
    (report_dir / "download_errors.csv").unlink(missing_ok=True)
    return manifest
