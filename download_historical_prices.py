
import argparse
from dotenv import load_dotenv

load_dotenv()

from market_data.downloader import GoldMarketDownloader


def main():
    parser = argparse.ArgumentParser(
        description="Download historical XAU/USD market data"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Download only the most recent N signals",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore existing cache and download again",
    )

    args = parser.parse_args()

    print("=" * 55)
    print("GOLD STRATEGY BACKTESTER")
    print("PHASE 2: OANDA MARKET DATA DOWNLOAD")
    print("=" * 55)

    downloader = GoldMarketDownloader(
        force=args.force
    )

    downloader.run(
        limit=args.limit
    )


if __name__ == "__main__":
    main()
