from dataclasses import dataclass
from typing import Optional

@dataclass
class ScheduleItem:
    """
    Domain model representing a single broadcast schedule item.
    """
    broadcast_date: str
    broadcast_time: str
    channel: str
    category: str
    product_name: str
    price: Optional[str]
    link_url: str
    estimated_sales: Optional[str] = None
    estimated_revenue: Optional[str] = None

    def to_dict(self) -> dict:
        """
        Converts the model to a dictionary for Excel/Pandas processing.
        Ensures batch assignment style where applicable.
        """
        data = {}
        data["방송날짜"] = self.broadcast_date
        data["방송시간"] = self.broadcast_time
        data["채널"] = self.channel
        data["카테고리"] = self.category
        data["상품명"] = self.product_name
        data["가격"] = self.price
        data["링크URL"] = self.link_url
        data["추정판매량"] = self.estimated_sales
        data["추정매출액"] = self.estimated_revenue
        return data
