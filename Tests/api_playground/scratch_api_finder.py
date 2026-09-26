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
        
        # Intercept XHR/Fetch
        async def handle_response(response):
            if "api" in response.url or "schedule" in response.url or "graphql" in response.url:
                if response.request.resource_type in ["fetch", "xhr"]:
                    print(f"API Intercepted: {response.request.method} {response.url}")
                    # try to dump a bit of the body if it's json
                    try:
                        json_data = await response.json()
                        print(f"JSON Key sample: {list(json_data.keys())[:5] if isinstance(json_data, dict) else 'List'}")
                    except:
                        pass
        
        page.on("response", handle_response)
        
        await page.goto("https://datahub.hsmoa.com/schedule")
        await page.wait_for_timeout(3000)
        
        print("Scrolling...")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        
        await browser.close()

asyncio.run(main())
