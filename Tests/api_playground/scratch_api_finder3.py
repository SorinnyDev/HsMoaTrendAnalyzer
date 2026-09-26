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
        
        print("Navigating to schedule...")
        await page.goto("https://datahub.hsmoa.com/schedule")
        await page.wait_for_timeout(3000)
        
        # Print out all links and button texts to understand the UI
        print("--- Buttons & Links ---")
        items = await page.query_selector_all("button, a")
        for item in items:
            text = await item.inner_text()
            href = await item.get_attribute("href")
            if text and text.strip():
                print(f"[{item.__class__.__name__}] Text: '{text.strip()}' | Href: '{href}'")
        
        print("--- Date Picker/Headers ---")
        # Find any element containing dates or '일'
        headers = await page.query_selector_all("h1, h2, h3, h4, .text-lg, .text-xl, div.flex")
        for h in headers[:20]:
            text = await h.inner_text()
            if text and ("월" in text or "일" in text or "202" in text):
                print(f"Header text: {text.strip()[:100]}")
        
        await browser.close()

asyncio.run(main())
