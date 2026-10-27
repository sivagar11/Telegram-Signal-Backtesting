
from pathlib import Path
import pandas as pd

SIGNALS = Path("data/signals/gold_signals_2026.csv")
PRICES = Path("data/prices/signal_windows")
OUTPUT = Path("reports/entry_diagnostics.csv")

ENTRY_DELAY_SECONDS = 5
MAX_QUOTE_WAIT_SECONDS = 15

signals = pd.read_csv(SIGNALS)
signals["timestamp_utc"] = pd.to_datetime(
    signals["timestamp_utc"], utc=True
)

results = []

for _, signal in signals.iterrows():
    message_id = int(signal["message_id"])
    direction = str(signal["direction"]).upper().strip()

    path = PRICES / f"signal_{message_id}_S5.csv"

    row = {
        "message_id": message_id,
        "timestamp_utc": signal["timestamp_utc"],
        "direction": direction,
        "entry_low": float(signal["entry_low"]),
        "entry_high": float(signal["entry_high"]),
        "oanda_entry": None,
        "distance_outside": None,
        "status": None,
    }

    if not path.exists():
        row["status"] = "MISSING_FILE"
        results.append(row)
        continue

    prices = pd.read_csv(path)

    if prices.empty:
        row["status"] = "NO_DATA"
        results.append(row)
        continue

    prices["time"] = pd.to_datetime(
        prices["time"], utc=True
    )

    assumed_time = (
        signal["timestamp_utc"]
        + pd.Timedelta(seconds=ENTRY_DELAY_SECONDS)
    )

    candidates = prices.loc[
        prices["time"] >= assumed_time
    ].sort_values("time")

    if candidates.empty:
        row["status"] = "NO_QUOTE"
        results.append(row)
        continue

    candle = candidates.iloc[0]

    wait = (
        candle["time"] - assumed_time
    ).total_seconds()

    if wait > MAX_QUOTE_WAIT_SECONDS:
        row["status"] = "LATE_QUOTE"
        results.append(row)
        continue

    if direction == "BUY":
        entry = float(candle["ask_o"])
    elif direction == "SELL":
        entry = float(candle["bid_o"])
    else:
        row["status"] = "INVALID_DIRECTION"
        results.append(row)
        continue

    low = row["entry_low"]
    high = row["entry_high"]

    # Absolute distance to the nearest range boundary.
    # Zero means the entry is inside the range.
    distance = max(low - entry, entry - high, 0)

    row["oanda_entry"] = entry
    row["distance_outside"] = round(distance, 3)
    row["quote_time_utc"] = candle["time"]
    row["status"] = (
        "INSIDE_RANGE"
        if distance == 0
        else "OUTSIDE_RANGE"
    )

    results.append(row)

df = pd.DataFrame(results)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUTPUT, index=False)

print("\nENTRY STATUS")
print(df["status"].value_counts())

outside = df[df["status"] == "OUTSIDE_RANGE"]

print("\nDISTANCE OUTSIDE RANGE")
print(outside["distance_outside"].describe())

print("\nDISTANCE DISTRIBUTION")
bins = [-0.001, 0.5, 1, 2, 5, 10, float("inf")]
labels = [
    "0-0.5", "0.5-1", "1-2",
    "2-5", "5-10", "Over 10"
]

print(
    pd.cut(
        outside["distance_outside"],
        bins=bins,
        labels=labels,
    ).value_counts().sort_index()
)

print("\nBY DIRECTION")
print(
    pd.crosstab(
        df["direction"],
        df["status"],
    )
)

print("\nLARGEST DIFFERENCES")
print(
    outside.nlargest(
        10, "distance_outside"
    )[
        [
            "message_id",
            "direction",
            "entry_low",
            "entry_high",
            "oanda_entry",
            "distance_outside",
        ]
    ].to_string(index=False)
)

print(f"\nSaved: {OUTPUT}")
