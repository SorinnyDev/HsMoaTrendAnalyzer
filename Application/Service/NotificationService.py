import os
import requests
import logging
from typing import List, Optional
from Infrastructure.Scrapers.MultiChannelAggregator import ChannelCrawlResult

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Application service for sending business reports and channel health statuses via Telegram.
    """
    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        self.base_url = "https://api.telegram.org/bot"
        self.token = token
        self.chat_id = chat_id
        
        if (not self.token):
            logger.error("TELEGRAM_BOT_TOKEN is missing in environment.")
        if (not self.chat_id):
            logger.error("TELEGRAM_CHAT_ID is missing in environment.")

    def format_channel_health_section(self, health_results: List[ChannelCrawlResult]) -> str:
        """
        Formats the channel crawl health status block for Telegram notifications.
        """
        if (not health_results):
            return ""

        lines: List[str] = []
        lines.append("[📡 채널별 편성표 수집 현황]")

        for result in health_results:
            if (result.is_success):
                item_count = len(result.items)
                line_str = f"• {result.channel_name}: 정상 수집 ({item_count}건)"
                lines.append(line_str)
            else:
                fail_days = result.consecutive_failures
                if (fail_days <= 0):
                    fail_days = 1
                err_msg = result.error_message
                if (not err_msg):
                    err_msg = "UnknownError"
                line_str = f"• {result.channel_name}: ⚠️ 실패 (연속 실패 {fail_days}일 / Error: {err_msg})"
                lines.append(line_str)

        return "\n".join(lines)

    def build_full_message(self, report_text: str, health_results: Optional[List[ChannelCrawlResult]] = None) -> str:
        """
        Combines the channel health check section and the Gemini AI report body.
        """
        if (health_results is not None and len(health_results) > 0):
            health_block = self.format_channel_health_section(health_results)
            full_text = f"{health_block}\n\n{report_text}"
            return full_text
        else:
            return report_text

    def send_report(self, report_text: str, health_results: Optional[List[ChannelCrawlResult]] = None) -> bool:
        """
        Sends the AI-generated report (with optional channel health check header) via Telegram Bot API.
        """
        if (not self.token):
            return False
        if (not self.chat_id):
            return False

        api_url = f"{self.base_url}{self.token}/sendMessage"
        final_message = self.build_full_message(report_text, health_results)
        
        # Batch Assignment rule: Explicitly assign each key
        payload = {}
        payload["chat_id"] = self.chat_id
        payload["text"] = final_message
        
        try:
            response = requests.post(api_url, data=payload)
            
            if (response.status_code == 200):
                logger.info("Telegram report sent successfully.")
                return True
            else:
                logger.error(f"Telegram API error: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {str(e)}")
            return False
