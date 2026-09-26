import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Tuple

class SqliteRepository:
    """
    Infrastructure layer for persisting crawling history using SQLite.
    Tracks which dates have been successfully scraped.
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(os.getenv("OUTPUT_DIR", "./outputs"), "crawling_history.db")
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize_db()

    def _initialize_db(self):
        """Creates the necessary tables if they don't exist."""
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
            # Index on target_date for faster lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_target_date ON crawling_history(target_date)
            ''')
            conn.commit()

    def mark_date_status(self, target_date: str, status: str, items_count: int = 0):
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

    def get_last_successful_date(self) -> Optional[str]:
        """
        Returns the latest target_date that has a 'SUCCESS' or 'NO_DATA' status.
        If no dates are collected, returns None.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # We consider 'SUCCESS' as successfully processed. 
            # We might reconsider 'NO_DATA' depending on if we want to retry it later or accept it as "there is truly no data".
            cursor.execute('''
                SELECT target_date FROM crawling_history 
                WHERE status = 'SUCCESS'
                ORDER BY target_date DESC LIMIT 1
            ''')
            row = cursor.fetchone()
            if row:
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
            return cursor.fetchone() is not None
