import asyncio
from playwright.async_api import async_playwright
import os
import json
from dotenv import load_dotenv

load_dotenv()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        user_id = os.getenv("ID")
        password = os.getenv("PASSWORD")
        
        await page.goto("https://datahub.hsmoa.com/login")
        await page.click("input#email")
        await page.type("input#email", user_id, delay=50)
        await page.click("input#password")
        await page.type("input#password", password, delay=50)
        await page.press("input#password", "Enter")
        # Wait for dashboard transition
        await page.wait_for_url("**/dashboard**", timeout=15000)
        print("Successfully logged in.")
        
        async def handle_response(response):
            try:
                # We want to catch GraphQL, trpc, or REST endpoints returning JSON
                if response.request.resource_type in ["fetch", "xhr"]:
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        url = response.url
                        if "google" not in url and "clarity" not in url and "iconify" not in url:
                            print(f"[API Endpoint Found]: {response.request.method} {url}")
                            try:
                                data = await response.json()
                                print(f"JSON Sample: {str(data)[:200]}")
                            except Exception as e:
                                print("Could not read json body:", e)
            except:
                pass
                
        page.on("response", handle_response)
        
        print("Navigating to schedule...")
        await page.goto("https://datahub.hsmoa.com/schedule", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        
        print("Scrolling down to trigger potential APIs...")
        # Scroll multiple times
        for i in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            
        await browser.close()

asyncio.run(main())
