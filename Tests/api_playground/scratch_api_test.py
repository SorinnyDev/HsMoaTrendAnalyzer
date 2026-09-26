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
        await page.wait_for_url("**/dashboard**", timeout=15000)
        
        # Now making the fetch call ourselves
        resp = await page.request.get("https://datahub.hsmoa.com/next-api/schedule?date=2026-04-16&start_hour=0&hour_num=24")
        data = await resp.json()
        print(f"Status: {resp.status}")
        
        # Count items
        total_items = 0
        categories = set()
        
        for section in ["before_live", "live", "after_live"]:
            if section in data:
                for block in data[section]:
                    for item in block.get("schedules", []):
                        total_items += 1
                        categories.add(item.get("category_name", "None"))
                        
        print(f"Total items found for day: {total_items}")
        print("Categories sample:", list(categories)[:5])
        
        await browser.close()

asyncio.run(main())
