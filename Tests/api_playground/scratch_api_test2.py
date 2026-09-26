import asyncio
from playwright.async_api import async_playwright
import os
import json
from dotenv import load_dotenv

load_dotenv()

async def main():
    p = await async_playwright().start()
    browser = await p.chromium.launch()
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://datahub.hsmoa.com/login")
    await page.fill("#email", os.getenv("ID"))
    await page.fill("#password", os.getenv("PASSWORD"))
    await page.press("#password", "Enter")
    await page.wait_for_url("**/dashboard**", timeout=15000)
    resp = await page.request.get("https://datahub.hsmoa.com/next-api/schedule?date=2026-04-16&start_hour=0&hour_num=1")
    data = await resp.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    await browser.close()
    await p.stop()

asyncio.run(main())
