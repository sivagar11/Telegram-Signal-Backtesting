from market_data.oanda_client import get_gold_candles

df = get_gold_candles(
    start="2026-10-20T08:00:00Z",
    end="2026-10-20T09:00:00Z"
)

print(df.head())
print(f"Downloaded {len(df)} candles")