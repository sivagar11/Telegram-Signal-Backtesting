
import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from telegram.parser import parse_signal


async def extract_signals(client, limit=None):
    group_id = int(os.environ["TELEGRAM_GROUP_ID"])

    output = Path("data/signals/gold_signals_2026.csv")
    output.parent.mkdir(parents=True, exist_ok=True)

    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    signals = []
    scanned = 0

    group = await client.get_entity(group_id)

    print(f"Reading group: {getattr(group, 'title', group_id)}")
    print(f"Collecting signals from {start_date.date()} to {end_date.date()}")

    # Newest messages first.
    async for message in client.iter_messages(
        group,
        limit=limit
    ):
        timestamp = message.date.astimezone(timezone.utc)

        # Stop once messages are older than 2026.
        if timestamp < start_date:
            break

        if timestamp > end_date:
            continue

        scanned += 1

        parsed = parse_signal(message.raw_text)

        if parsed is None:
            continue

        parsed["message_id"] = message.id
        parsed["timestamp_utc"] = timestamp.isoformat()
        parsed["original_message"] = message.raw_text

        signals.append(parsed)

        print(
            f"{timestamp.date()} "
            f"{parsed['direction']} "
            f"{parsed['entry_low']}-"
            f"{parsed['entry_high']} "
            f"SL={parsed['stop_loss']} "
            f"TP1={parsed['tp1']}"
        )

    # Sort from oldest to newest for backtesting.
    signals.sort(key=lambda s: s["timestamp_utc"])

    if signals:
        with output.open(
            "w",
            newline="",
            encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=list(signals[0].keys())
            )
            writer.writeheader()
            writer.writerows(signals)

    print("\nExtraction completed!")
    print(f"Messages scanned: {scanned}")
    print(f"2026 signals extracted: {len(signals)}")

    if signals:
        print(f"First signal: {signals[0]['timestamp_utc']}")
        print(f"Last signal: {signals[-1]['timestamp_utc']}")
        print(f"Saved to: {output.resolve()}")
    else:
        print("No matching 2026 signals found.")


