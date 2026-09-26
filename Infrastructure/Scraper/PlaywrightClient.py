import os
import asyncio
import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PlaywrightClient:
    """
    Infrastructure layer for web scraping using Playwright.
    Provides base functionality for authentication and page navigation.
    """

    def __init__(self) -> None:
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright_manager = None

    async def initialize(self, headless: bool = True) -> None:
        """
        Initializes the Playwright browser and context.
        """
        logger.info("Initializing Playwright browser...")
        self.playwright_manager = await async_playwright().start()
        self.browser = await self.playwright_manager.chromium.launch(headless=headless)
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()

    async def login(self, login_url: str) -> bool:
        """
        Handles the login process for HsMoa DataHub.
        Uses environment variables for credentials.
        """
        user_id = os.getenv("ID")
        password = os.getenv("PASSWORD")

        if (not user_id or not password):
            logger.error("Login credentials not found in environment variables.")
            return False

        logger.info(f"Navigating to login page: {login_url}")
        
        try:
            await self.page.goto(login_url)
            
            # Explicitly filling identity fields
            # Selectors found via browser exploration: #email, #password
            await self.page.click("input#email")
            await self.page.type("input#email", user_id, delay=150)
            
            await self.page.click("input#password")
            await self.page.type("input#password", password, delay=200)
            
            # Pressing Enter to submit
            await self.page.press("input#password", "Enter")
            
            # Wait a bit for transition
            await self.page.wait_for_timeout(10000)
            await self.page.wait_for_load_state("networkidle")
            
            # Verify if login was successful
            current_url = self.page.url
            logger.info(f"Final login URL: {current_url}")
            if ("login" in current_url):
                logger.warning("Login might have failed. Taking screenshot...")
                await self.page.screenshot(path="login_failure.png")
                # Check for error message text on page
                error_msg = await self.page.query_selector(".text-red-500, .error-message, div:has-text('올바르지 않습니다')")
                if (error_msg):
                    msg_text = await error_msg.inner_text()
                    logger.error(f"Site error message: {msg_text}")
                return False
            
            logger.info("Login successful.")
            return True

        except Exception as e:
            logger.error(f"An error occurred during login: {str(e)}")
            return False

    async def navigate_to(self, url: str) -> bool:
        """
        Navigates to a specific URL and waits for content to load.
        """
        if (not self.page):
            logger.error("Page object is not initialized. Call initialize() first.")
            return False

        logger.info(f"Navigating to {url}...")
        try:
            response = await self.page.goto(url)
            if (response is None):
                logger.error(f"Failed to get response from {url}")
                return False
            
            if (response.status != 200):
                logger.warning(f"Response status for {url} is {response.status}")
                # We still might want to proceed if it's not a hard error
            
            await self.page.wait_for_load_state("networkidle")
            # Extra wait for dynamic frameworks to settle
            await self.page.wait_for_timeout(2000)
            return True
        except Exception as e:
            logger.error(f"Error navigating to {url}: {str(e)}")
            return False

    async def close(self) -> None:
        """
        Closes the browser and stops Playwright.
        """
        if (self.browser):
            await self.browser.close()
        if (self.playwright_manager):
            await self.playwright_manager.stop()
        logger.info("Playwright browser closed.")

if __name__ == "__main__":
    # Basic sanity check
    async def main():
        client = PlaywrightClient()
        await client.initialize(headless=False)
        # To be tested with real env vars
        # await client.login("https://datahub.hsmoa.com/login")
        await client.close()

    asyncio.run(main())
