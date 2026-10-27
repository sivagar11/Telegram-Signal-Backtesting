
import json
import os
from pathlib import Path

import pandas as pd

from market_data.oanda_client import (
    OandaClient,
    as_utc,
    GRANULARITY_SECONDS,
)

SIGNALS_FILE = Path(
    "data/signals/gold_signals_2026.csv"
)

PRICE_DIR = Path("data/prices")
CACHE_DIR = PRICE_DIR / "cache"
WINDOW_DIR = PRICE_DIR / "signal_windows"
MANIFEST_FILE = PRICE_DIR / "download_manifest.csv"

ENTRY_DELAY = int(
    os.getenv("ENTRY_DELAY_SECONDS", "5")
)

ENTRY_DATA_MINUTES = int(
    os.getenv("ENTRY_DATA_MINUTES", "3")
)

MONITOR_HOURS = float(
    os.getenv("TRADE_MONITOR_HOURS", "5")
)

INSTRUMENT = os.getenv(
    "OANDA_INSTRUMENT", "XAU_USD"
)

ENVIRONMENT = os.getenv(
    "OANDA_ENV", "practice"
)


def load_signals():
    df = pd.read_csv(SIGNALS_FILE)

    required = {
        "message_id",
        "timestamp_utc",
        "direction",
        "entry_low",
        "entry_high",
        "stop_loss",
        "tp1",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing columns: {missing}"
        )

    df["timestamp_utc"] = pd.to_datetime(
        df["timestamp_utc"],
        utc=True,
        errors="raise",
    )

    if df["timestamp_utc"].isna().any():
        raise ValueError(
            "Invalid Telegram timestamps"
        )

    if df["message_id"].duplicated().any():
        raise ValueError(
            "Duplicate Telegram message IDs"
        )

    return df.sort_values(
        "timestamp_utc"
    ).reset_index(drop=True)


class GoldMarketDownloader:
    def __init__(self, force=False):
        self.client = OandaClient()
        self.force = force

        for directory in (
            CACHE_DIR, WINDOW_DIR
        ):
            directory.mkdir(
                parents=True,
                exist_ok=True,
            )

    def cache_file(
        self, message_id, granularity
    ):
        return CACHE_DIR / (
            f"{INSTRUMENT}_{ENVIRONMENT}_BA_"
            f"{message_id}_{granularity}.csv"
        )

    def get_prices(
        self,
        message_id,
        start,
        end,
        granularity,
    ):
        path = self.cache_file(
            message_id, granularity
        )

        metadata_path = path.with_suffix(
            ".json"
        )

        start = as_utc(start)
        end = as_utc(end)

        now = pd.Timestamp.now(tz="UTC")

        # Never request future data.
        effective_end = min(end, now)

        metadata = {
            "start": start.isoformat(),
            "end": effective_end.isoformat(),
            "granularity": granularity,
            "instrument": INSTRUMENT,
            "environment": ENVIRONMENT,
            "price": "BA",
        }

        # Cache is valid only for an identical request.
        if (
            not self.force
            and path.exists()
            and metadata_path.exists()
        ):
            try:
                saved_metadata = json.loads(
                    metadata_path.read_text(
                        encoding="utf-8"
                    )
                )

                if saved_metadata == metadata:
                    df = pd.read_csv(path)

                    if df.empty:
                        return pd.DataFrame()

                    df["time"] = pd.to_datetime(
                        df["time"], utc=True
                    )

                    return df
            except (
                ValueError,
                OSError,
                KeyError,
            ):
                pass

        if effective_end <= start:
            return pd.DataFrame()

        print(
            f"  Downloading {granularity}: "
            f"{start} -> {effective_end}"
        )

        df = self.client.get_candles(
            start=start,
            end=effective_end,
            granularity=granularity,
        )

        # Save only complete historical windows.
        # Partial current-day windows will be retried.
        if end <= now:
            df.to_csv(
                path, index=False
            )

            metadata_path.write_text(
                json.dumps(
                    metadata, indent=2
                ),
                encoding="utf-8",
            )

        return df

    def download_signal(self, signal):
        message_id = int(
            signal["message_id"]
        )

        signal_time = as_utc(
            signal["timestamp_utc"]
        )

        entry_time = (
            signal_time
            + pd.Timedelta(
                seconds=ENTRY_DELAY
            )
        )

        # 5-second candles begin at the first
        # boundary on or after the assumed entry time.
        s5_start = entry_time.floor("5s")

        s5_end = (
            entry_time
            + pd.Timedelta(
                minutes=ENTRY_DATA_MINUTES
            )
        ).ceil("min")

        # M1 monitoring starts from the beginning
        # of the signal's minute. The backtester
        # must ignore pre-entry movements.
        m1_start = signal_time.floor("min")

        m1_end = (
            signal_time
            + pd.Timedelta(
                hours=MONITOR_HOURS
            )
        ).ceil("min")

        s5 = self.get_prices(
            message_id,
            s5_start,
            s5_end,
            "S5",
        )

        m1 = self.get_prices(
            message_id,
            m1_start,
            m1_end,
            "M1",
        )

        s5_path = WINDOW_DIR / (
            f"signal_{message_id}_S5.csv"
        )

        m1_path = WINDOW_DIR / (
            f"signal_{message_id}_M1.csv"
        )

        s5.to_csv(
            s5_path, index=False
        )

        m1.to_csv(
            m1_path, index=False
        )

        # Only candles that have fully completed
        # by the time of this download are usable.
        now = pd.Timestamp.now(tz="UTC")

        expected_s5_complete = s5_end <= now
        expected_m1_complete = m1_end <= now

        if (
            not expected_s5_complete
            or not expected_m1_complete
        ):
            status = "PARTIAL_WINDOW"
        elif s5.empty or m1.empty:
            status = "MISSING_MARKET_DATA"
        else:
            status = "DOWNLOADED"

        return {
            "message_id": message_id,
            "signal_time_utc": (
                signal_time.isoformat()
            ),
            "direction": signal["direction"],
            "entry_low": signal["entry_low"],
            "entry_high": signal["entry_high"],
            "stop_loss": signal["stop_loss"],
            "tp1": signal["tp1"],
            "assumed_entry_time_utc": (
                entry_time.isoformat()
            ),
            "s5_start_utc": (
                s5_start.isoformat()
            ),
            "s5_end_utc": (
                s5_end.isoformat()
            ),
            "m1_start_utc": (
                m1_start.isoformat()
            ),
            "m1_end_utc": (
                m1_end.isoformat()
            ),
            "s5_candles": len(s5),
            "m1_candles": len(m1),
            "s5_file": str(s5_path),
            "m1_file": str(m1_path),
            "status": status,
        }

    def run(self, limit=None):
        signals = load_signals()

        if limit is not None:
            signals = signals.tail(limit)

        print(
            f"\nSignals to process: {len(signals)}"
        )

        print(
            f"Entry delay: {ENTRY_DELAY} seconds"
        )

        print(
            f"Monitoring window: {MONITOR_HOURS} hours"
        )

        results = []

        try:
            for index, (
                _, signal
            ) in enumerate(
                signals.iterrows(),
                start=1,
            ):
                message_id = int(
                    signal["message_id"]
                )

                print(
                    f"\n[{index}/{len(signals)}] "
                    f"Signal {message_id}"
                )

                try:
                    result = self.download_signal(
                        signal
                    )

                    print(
                        f"  S5: {result['s5_candles']} | "
                        f"M1: {result['m1_candles']} | "
                        f"{result['status']}"
                    )

                except Exception as exc:
                    print(f"  ERROR: {exc}")

                    result = {
                        "message_id": message_id,
                        "status": "ERROR",
                        "error": str(exc),
                    }

                results.append(result)

                # Save progress after each signal.
                pd.DataFrame(results).to_csv(
                    MANIFEST_FILE,
                    index=False,
                )

        finally:
            self.client.close()

        report = pd.DataFrame(results)

        print("\n" + "=" * 50)
        print("DOWNLOAD SUMMARY")
        print("=" * 50)

        print(
            report["status"].value_counts()
        )

        print(
            f"\nManifest: {MANIFEST_FILE}"
        )

        return report
