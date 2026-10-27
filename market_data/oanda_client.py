
import os
import time
import random
from datetime import timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

GRANULARITY_SECONDS = {
    "S5": 5,
    "M1": 60,
}

PRICE_COLUMNS = [
    "time",
    "bid_o", "bid_h", "bid_l", "bid_c",
    "ask_o", "ask_h", "ask_l", "ask_c",
]


def as_utc(value):
    ts = pd.Timestamp(value)

    if ts.tzinfo is None:
        raise ValueError(
            f"Timezone missing from timestamp: {value}"
        )

    return ts.tz_convert("UTC")


def api_time(value):
    return as_utc(value).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class OandaClient:
    def __init__(self):
        api_key = os.environ["OANDA_API_KEY"]
        environment = os.getenv(
            "OANDA_ENV", "practice"
        ).lower()

        if environment not in ("practice", "live"):
            raise ValueError(
                "OANDA_ENV must be practice or live"
            )

        host = (
            "api-fxpractice.oanda.com"
            if environment == "practice"
            else "api-fxtrade.oanda.com"
        )

        instrument = os.getenv(
            "OANDA_INSTRUMENT", "XAU_USD"
        )

        self.url = (
            f"https://{host}/v3/instruments/"
            f"{instrument}/candles"
        )

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept-Datetime-Format": "RFC3339",
        })

    def request(self, params):
        for attempt in range(6):
            try:
                response = self.session.get(
                    self.url,
                    params=params,
                    timeout=45,
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code not in (
                    429, 500, 502, 503, 504
                ):
                    response.raise_for_status()

                if attempt == 5:
                    response.raise_for_status()

                retry_after = response.headers.get(
                    "Retry-After"
                )

                try:
                    wait = (
                        float(retry_after)
                        if retry_after
                        else min(2 ** attempt, 30)
                    )
                except ValueError:
                    wait = min(2 ** attempt, 30)

            except requests.RequestException:
                if attempt == 5:
                    raise
                wait = min(2 ** attempt, 30)

            time.sleep(
                min(max(wait, 0), 120)
                + random.uniform(0, 0.5)
            )

        raise RuntimeError("OANDA request failed")

    def get_candles(
        self,
        start,
        end,
        granularity="M1",
    ):
        start = as_utc(start)
        end = as_utc(end)

        if granularity not in GRANULARITY_SECONDS:
            raise ValueError(
                f"Unsupported granularity: {granularity}"
            )

        if start >= end:
            return pd.DataFrame(
                columns=PRICE_COLUMNS
            )

        interval = pd.Timedelta(
            seconds=GRANULARITY_SECONDS[granularity]
        )

        # OANDA permits at most 5000 candles.
        # Request <= 4999 intervals per page.
        max_span = interval * 4999

        rows = []
        cursor = start

        while cursor < end:
            chunk_end = min(
                cursor + max_span,
                end
            )

            params = {
                "from": api_time(cursor),
                "to": api_time(chunk_end),
                "granularity": granularity,
                "price": "BA",
                "includeFirst": "true",
            }

            payload = self.request(params)

            for candle in payload.get(
                "candles", []
            ):
                if not candle.get(
                    "complete", False
                ):
                    continue

                if (
                    "bid" not in candle
                    or "ask" not in candle
                ):
                    continue

                row = {
                    "time": as_utc(
                        candle["time"]
                    )
                }

                for side in ("bid", "ask"):
                    for field in (
                        "o", "h", "l", "c"
                    ):
                        row[f"{side}_{field}"] = (
                            float(
                                candle[side][field]
                            )
                        )

                rows.append(row)

            # Adjacent pages overlap at the boundary.
            # Duplicates are removed below.
            cursor = chunk_end

        if not rows:
            return pd.DataFrame(
                columns=PRICE_COLUMNS
            )

        df = pd.DataFrame(rows)

        df = (
            df.drop_duplicates(
                subset=["time"]
            )
            .sort_values("time")
            .reset_index(drop=True)
        )

        # Candle timestamp is the opening time.
        # End is exclusive.
        df = df.loc[
            (df["time"] >= start)
            & (df["time"] < end)
        ].copy()

        return df.reset_index(drop=True)

    def close(self):
        self.session.close()


# Keep compatibility with your working test_oanda.py.
def get_gold_candles(
    start, end, granularity="M1"
):
    client = OandaClient()
    try:
        return client.get_candles(
            start, end, granularity
        )
    finally:
        client.close()
