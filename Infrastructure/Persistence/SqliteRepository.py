import sqlite3
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class SqliteRepository:
    """
    Infrastructure layer for persisting crawling history and channel health status using SQLite.
    Tracks which dates have been scraped and monitors consecutive failures per channel.
    """

    def __init__(self, db_path: str = None) -> None:
        if (db_path is not None):
            self.db_path = db_path
        else:
            self.db_path = os.path.join(os.getenv("OUTPUT_DIR", "./outputs"), "crawling_history.db")

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize_db()

    def _initialize_db(self) -> None:
        """
        Creates crawling_history and channel_health_history tables if they do not exist.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS crawling_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_date TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    items_count INTEGER DEFAULT 0,
                    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_target_date ON crawling_history(target_date)
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channel_health_history (
                    channel_name TEXT PRIMARY KEY,
                    last_status TEXT NOT NULL,
                    consecutive_failures INTEGER DEFAULT 0,
                    last_error_message TEXT DEFAULT '',
                    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def mark_date_status(self, target_date: str, status: str, items_count: int = 0) -> None:
        """
        Marks a specific date with a status ('SUCCESS', 'NO_DATA', 'FAILED').
        target_date format: 'YYYY-MM-DD'
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute('''
                INSERT INTO crawling_history (target_date, status, items_count, crawled_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target_date) DO UPDATE SET
                    status=excluded.status,
                    items_count=excluded.items_count,
                    crawled_at=excluded.crawled_at
            ''', (target_date, status, items_count, now))
            conn.commit()

    def update_channel_health(self, channel_name: str, is_success: bool, error_message: str = "") -> int:
        """
        Updates channel health status in channel_health_history table.
        - On success: consecutive_failures = 0, last_status = 'SUCCESS'
        - On failure: consecutive_failures += 1, last_status = 'FAILURE' and logs detailed error.
        Returns the updated consecutive_failures count.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated_failures = 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT consecutive_failures FROM channel_health_history
                WHERE channel_name = ?
            ''', (channel_name,))
            row = cursor.fetchone()

            prev_failures = 0
            if (row is not None):
                prev_failures = int(row[0])

            if (is_success):
                updated_failures = 0
                status_str = "SUCCESS"
                clean_error = ""
            else:
                updated_failures = prev_failures + 1
                status_str = "FAILURE"
                clean_error = error_message
                logger.error(
                    f"[HealthCheck] Channel '{channel_name}' failed ({updated_failures} consecutive days). Error: {clean_error}"
                )

            cursor.execute('''
                INSERT INTO channel_health_history (
                    channel_name, last_status, consecutive_failures, last_error_message, last_updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_name) DO UPDATE SET
                    last_status=excluded.last_status,
                    consecutive_failures=excluded.consecutive_failures,
                    last_error_message=excluded.last_error_message,
                    last_updated_at=excluded.last_updated_at
            ''', (channel_name, status_str, updated_failures, clean_error, now_str))
            conn.commit()

        return updated_failures

    def get_all_channel_health(self) -> List[Dict[str, Any]]:
        """
        Retrieves all rows from channel_health_history as a list of dictionaries.
        """
        records: List[Dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT channel_name, last_status, consecutive_failures, last_error_message, last_updated_at
                FROM channel_health_history
            ''')
            rows = cursor.fetchall()
            for row in rows:
                entry = {}
                entry["channel_name"] = row[0]
                entry["last_status"] = row[1]
                entry["consecutive_failures"] = int(row[2])
                entry["last_error_message"] = row[3]
                entry["last_updated_at"] = row[4]
                records.append(entry)
        return records

    def get_last_successful_date(self) -> Optional[str]:
        """
        Returns the latest target_date that has a 'SUCCESS' status.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT target_date FROM crawling_history
                WHERE status = 'SUCCESS'
                ORDER BY target_date DESC LIMIT 1
            ''')
            row = cursor.fetchone()
            if (row is not None):
                return row[0]
            return None

    def is_date_crawled(self, target_date: str) -> bool:
        """
        Checks if a specific date was already successfully crawled.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 1 FROM crawling_history
                WHERE target_date = ? AND status = 'SUCCESS'
            ''', (target_date,))
            row = cursor.fetchone()
            if (row is not None):
                return True
            else:
                return False
