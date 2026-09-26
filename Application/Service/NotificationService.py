import os
import requests
import logging

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Application service for sending business reports via Telegram.
    """
    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        # Explicit assignment instead of template strings in init if possible
        self.base_url = "https://api.telegram.org/bot"
        self.token = token
        self.chat_id = chat_id
        
        if (not self.token):
            logger.error("TELEGRAM_BOT_TOKEN is missing in environment.")
        if (not self.chat_id):
            logger.error("TELEGRAM_CHAT_ID is missing in environment.")

    def send_report(self, report_text: str) -> bool:
        """
        Sends the AI-generated report via Telegram Bot API.
        """
        if (not self.token):
            return False
        if (not self.chat_id):
            return False

        api_url = f"{self.base_url}{self.token}/sendMessage"
        
        # Batch Assignment rule: Explicitly assign each key
        payload = {}
        payload["chat_id"] = self.chat_id
        payload["text"] = report_text
        
        try:
            # Explicit if block
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
