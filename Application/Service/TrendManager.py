import os
import pandas as pd
import logging
from datetime import datetime
from typing import List, Dict
from Infrastructure.AI.GeminiClient import GeminiClient
from Application.Service.WeightCalculator import WeightCalculator
from Domain.Models.Schedule import ScheduleItem

logger = logging.getLogger(__name__)

class TrendManager:
    """
    Application service that coordinates Gemini analysis, weight calculation, and manages cumulative statistics.
    """
    def __init__(self) -> None:
        self.gemini_client = GeminiClient()
        self.weight_calculator = WeightCalculator()
        self.stats_file_path = os.path.join(os.getenv("OUTPUT_DIR", "./outputs"), "건기식 - statistics.xlsx")
        
        # Explicit directory creation
        os.makedirs(os.getenv("OUTPUT_DIR", "./outputs"), exist_ok=True)
        
    async def process_daily_trends(self, items: List[ScheduleItem], target_date: str) -> None:
        """
        Analyzes items to extract ingredients/brands and appends statistics to the cumulative file.
        target_date: 'YYYY-MM-DD'
        """
        if (not items):
            logger.warning("No items provided for trend analysis.")
            return

        logger.info(f"Processing trends for {len(items)} items on {target_date}...")

        # Caching analyzed results for same product names to reduce API calls
        product_cache = {}
        processed_entries = []
        
        for item in items:
            name = item.product_name
            time = item.broadcast_time
            
            # 1. Analyze with Gemini (cached)
            if (name not in product_cache):
                analysis_result = self.gemini_client.analyze_product_name(name)
                product_cache[name] = analysis_result
            
            resolved = product_cache[name]
            
            # 2. Calculate Weight based on broadcast time
            item_weight = self.weight_calculator.get_weight(time)
            
            # Explicit assignment for each processed item
            entry = {}
            entry["brand"] = resolved["brand"]
            entry["ingredient"] = resolved["ingredient"]
            entry["weight"] = item_weight
            processed_entries.append(entry)
            
        # Create DataFrame for aggregation
        df_raw = pd.DataFrame(processed_entries)
        
        # Aggregate by Ingredient
        daily_stats = []
        unique_ingredients = df_raw["ingredient"].unique()
        
        for ing in unique_ingredients:
            # Filter group for this ingredient
            group = df_raw[df_raw["ingredient"] == ing]
            
            # Calculate Sum of weights (Power Score)
            total_power_score = group["weight"].sum()
            
            unique_brands = group["brand"].unique().tolist()
            brand_list_str = ", ".join(unique_brands)
            
            # Batch Assignment: Explicitly set each key for the statistics row
            stats_row = {}
            stats_row["날짜"] = target_date
            stats_row["원료명"] = ing
            stats_row["방송 횟수"] = len(group)
            stats_row["Power Score"] = round(total_power_score, 1)
            stats_row["Weighted Count"] = round(total_power_score, 1)
            stats_row["참여 브랜드 리스트"] = brand_list_str
            
            daily_stats.append(stats_row)
            
        df_daily = pd.DataFrame(daily_stats)
        
        # Save or Append logic
        self._save_cumulative_stats(df_daily)

    def _save_cumulative_stats(self, df_new: pd.DataFrame) -> None:
        """
        Strict logic for loading existing file and appending new results.
        """
        try:
            if (os.path.exists(self.stats_file_path)):
                # Load existing
                df_existing = pd.read_excel(self.stats_file_path)
                
                # Append new data
                df_cumulative = pd.concat([df_existing, df_new], ignore_index=True)
                
                # Save back
                df_cumulative.to_excel(self.stats_file_path, index=False)
                logger.info(f"Successfully appended {len(df_new)} rows to existing statistics.")
            else:
                # Create new
                df_new.to_excel(self.stats_file_path, index=False)
                logger.info(f"Created new statistics file with {len(df_new)} rows.")
                
        except Exception as e:
            logger.error(f"Failed to update cumulative statistics: {str(e)}")
