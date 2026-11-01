import os
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import List, Dict
from Application.Service.WeightCalculator import WeightCalculator
from Domain.Models.Schedule import ScheduleItem

logger = logging.getLogger(__name__)

class SyncManager:
    """
    Infrastructure service for synchronizing 7-day (D+0 ~ D+6) forecast schedule.
    Handles merging new data with existing files using primary keys (Date+Time+Channel).
    """
    def __init__(self) -> None:
        self.output_dir = os.getenv("OUTPUT_DIR", "./outputs")
        self.sync_file_path = os.path.join(self.output_dir, "7day_forecast_sync.xlsx")
        self.weight_calculator = WeightCalculator()
        
        if (not os.path.exists(self.output_dir)):
            os.makedirs(self.output_dir)
        
    def synchronize_window_data(self, new_items: List[ScheduleItem], window_days: int = 7) -> None:
        """
        Upserts new items into the D+7 (D+0 ~ D+6) sliding window file and removes expired/out-of-window dates.
        """
        today_date = datetime.now().date()
        today_str = today_date.strftime("%Y-%m-%d")
        max_date_str = (today_date + timedelta(days=window_days - 1)).strftime("%Y-%m-%d")
        
        # 1. Load and Clean Existing Data
        df_old = pd.DataFrame()
        if (os.path.exists(self.sync_file_path)):
            try:
                df_old = pd.read_excel(self.sync_file_path)
                if (not df_old.empty):
                    df_old["broadcast_date"] = df_old["broadcast_date"].astype(str)
                    df_old = df_old[df_old["broadcast_date"] >= today_str]
                    df_old = df_old[df_old["broadcast_date"] <= max_date_str]
            except Exception as e:
                logger.error(f"Failed to load existing sync file: {str(e)}")

        # 2. Prepare New Data with Power Scores
        new_records = []
        for item in new_items:
            try:
                current_weight = self.weight_calculator.get_weight(item.broadcast_time)
                
                row = {}
                row["broadcast_date"] = str(item.broadcast_date)
                row["broadcast_time"] = str(item.broadcast_time)
                row["channel"] = item.channel
                row["product_name"] = item.product_name
                row["category"] = item.category
                row["price"] = item.price
                row["power_score"] = current_weight
                row["sync_key"] = f"{item.broadcast_date}_{item.broadcast_time}_{item.channel}"
                
                new_records.append(row)
            except Exception as e:
                logger.error(f"Error processing item for sync: {str(e)}")
                
        df_new = pd.DataFrame(new_records)
        
        # 3. Merge (Upsert)
        if (df_old.empty):
            df_final = df_new
        elif (df_new.empty):
            df_final = df_old
        else:
            if ("sync_key" not in df_old.columns):
                df_old["sync_key"] = df_old["broadcast_date"].astype(str) + "_" + df_old["broadcast_time"].astype(str) + "_" + df_old["channel"].astype(str)
            
            df_combined = pd.concat([df_new, df_old], ignore_index=True)
            df_final = df_combined.drop_duplicates(subset=["sync_key"], keep="first")

        # 4. Final Save
        try:
            df_final.to_excel(self.sync_file_path, index=False)
            logger.info(f"7-Day Sliding Window Sync Successful. Total active slots: {len(df_final)}")
        except Exception as e:
            logger.error(f"Failed to save synchronized data: {str(e)}")
            
    def get_current_window_items(self) -> List[Dict]:
        """
        Retrieves all currently active items in the 7-day window as a list of dicts.
        """
        if (os.path.exists(self.sync_file_path)):
            try:
                df = pd.read_excel(self.sync_file_path)
                if (df.empty):
                    return []
                today_str = datetime.now().strftime("%Y-%m-%d")
                df["broadcast_date"] = df["broadcast_date"].astype(str)
                df = df[df["broadcast_date"] >= today_str]
                return df.to_dict("records")
            except Exception as e:
                logger.error(f"Error reading sync file for retrieval: {str(e)}")
                return []
        return []
