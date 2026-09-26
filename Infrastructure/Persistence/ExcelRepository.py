import os
import pandas as pd
from typing import List
from Domain.Models.Schedule import ScheduleItem

class ExcelRepository:
    """
    Infrastructure layer for persisting data to Excel files.
    Handles monthly files and daily sheets.
    """

    def __init__(self, base_path: str = "./outputs") -> None:
        self.base_path = base_path
        if (not os.path.exists(self.base_path)):
            os.makedirs(self.base_path)

    def save_schedule(self, items: List[ScheduleItem], month_str: str, sheet_name: str) -> str:
        """
        Saves a list of schedule items to a specific tab in a monthly Excel file.
        """
        file_name = f"홈쇼핑모아 편성표 - {month_str}.xlsx"
        file_path = os.path.join(self.base_path, file_name)
        
        # Convert items to DataFrame
        df = pd.DataFrame([item.to_dict() for item in items])
        
        # Check if file exists to append or create new
        if (os.path.exists(file_path)):
            with pd.ExcelWriter(file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        else:
            with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        return file_path
