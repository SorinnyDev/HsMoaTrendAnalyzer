from datetime import datetime, timedelta
from typing import List
import logging

logger = logging.getLogger(__name__)

class WindowService:
    """
    Domain service to manage the 7-day sliding window (D+0 ~ D+6, Today + 6 days).
    """
    def __init__(self, window_size: int = 7) -> None:
        self.window_size = window_size

    def get_sliding_window_dates(self) -> List[str]:
        """
        Returns a list of window_size (default 7) dates from today in YYYY-MM-DD format.
        """
        target_dates = []
        today = datetime.now().date()
        
        for i in range(self.window_size):
            current_date = today + timedelta(days=i)
            date_str = current_date.strftime("%Y-%m-%d")
            target_dates.append(date_str)
            
        return target_dates

    def filter_past_data(self, date_str: str) -> bool:
        """
        Determines if a record date is outdated (older than today).
        """
        try:
            today = datetime.now().date()
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            
            if (target_date < today):
                return True
            else:
                return False
        except Exception as e:
            logger.error(f"Error parsing date for filter: {str(e)}")
            return False
