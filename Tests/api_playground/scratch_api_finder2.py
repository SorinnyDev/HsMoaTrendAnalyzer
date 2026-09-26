import asyncio
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        user_id = os.getenv("ID")
        password = os.getenv("PASSWORD")
        
        print("Logging in...")
        await page.goto("https://datahub.hsmoa.com/login")
        await page.click("input#email")
        await page.type("input#email", user_id, delay=50)
        await page.click("input#password")
        await page.type("input#password", password, delay=50)
        await page.press("input#password", "Enter")
        await page.wait_for_load_state("networkidle")
        
        print("Logged in. Navigating to schedule and intercepting APIs...")
        
        async def handle_response(response):
            # Print URLs of all requests that are not images, css or analytics
            if response.request.resource_type in ["xhr", "fetch", "document", "script"]:
                if "google" not in response.url and "icon" not in response.url:
                    print(f"[{response.request.resource_type}] {response.url}")
        
        page.on("response", handle_response)
        
        await page.goto("https://datahub.hsmoa.com/schedule")
        await page.wait_for_timeout(3000)
        
        print("Scrolling 1...")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        
        print("Scrolling 2...")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        
        await browser.close()

asyncio.run(main())
