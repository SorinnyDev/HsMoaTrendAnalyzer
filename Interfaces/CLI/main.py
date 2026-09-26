import asyncio
import argparse
import sys
import os

# Add root directory to python path for correct importing
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
from Application.Services.TrendAnalysisService import TrendAnalysisService

async def async_main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="HsMoa Trend Analyzer - CLI Entry Point")
    parser.add_argument("--date", type=str, help="Target date to scrape (format: YYYY-MM-DD)", default=None)
    parser.add_argument("--auto", action="store_true", help="Run automated continuous crawl loop based on DB history")
    parser.add_argument("--mode", type=str, choices=["daily", "forecast"], default="daily", help="Operation mode")
    parser.add_argument("--send-report", action="store_true", help="Send AI summary report via Telegram")
    
    args = parser.parse_args()
    
    service = TrendAnalysisService()
    
    # Explicit if-elif blocks with parentheses
    if (args.mode == "forecast"):
        # Execute the new 14-day sliding window sync process
        await service.run_sliding_window_sync()
            
    elif (args.auto):
        # Legacy/Automated catch-all mode now uses sync logic for robustness
        await service.run_sliding_window_sync()
        
    else:
        # Single date collection
        await service.run_daily_collection(args.date)
        if (args.send_report):
            await service.generate_and_send_report()

def main():
    asyncio.run(async_main())

if __name__ == "__main__":
    main()
