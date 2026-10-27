import re


def parse_signal(message: str):
    if not message:
        return None

    text = message.upper().strip()

    direction = re.search(
        r"\bXAU\s*/?\s*USD\s*[:\-]?\s*(BUY|SELL)\b",
        text
    )

    entry = re.search(
        r"@\s*(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)",
        text
    )

    sl = re.search(
        r"\bSL\b[^\d\n]*(\d+(?:\.\d+)?)",
        text
    )

    tps = re.findall(
        r"\bTP\d*\b[^\d\n]*(\d+(?:\.\d+)?)",
        text
    )

    if not all([direction, entry, sl]) or not tps:
        return None

    entry_low, entry_high = sorted([
        float(entry.group(1)),
        float(entry.group(2))
    ])

    stop_loss = float(sl.group(1))
    take_profits = [float(tp) for tp in tps]
    side = direction.group(1)

    if side == "BUY":
        valid = (
            stop_loss < entry_low
            and all(tp > entry_high for tp in take_profits)
        )
    else:
        valid = (
            stop_loss > entry_high
            and all(tp < entry_low for tp in take_profits)
        )

    if not valid:
        return None

    return {
        "symbol": "XAUUSD",
        "direction": side,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "tp1": take_profits[0],
        "tp2": take_profits[1] if len(take_profits) > 1 else None,
        "tp3": take_profits[2] if len(take_profits) > 2 else None,
    }