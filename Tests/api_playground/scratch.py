import asyncio
from Infrastructure.Scraper.ScheduleScraper import ScheduleScraper
from dotenv import load_dotenv

load_dotenv()

async def main():
    scraper = ScheduleScraper()
    await scraper.initialize(headless=True)
    await scraper.login("https://datahub.hsmoa.com/login")
    
    date_str = "2026-04-18"
    api_url = f"https://datahub.hsmoa.com/next-api/schedule?date={date_str}&start_hour=0&hour_num=24"
    resp = await scraper.page.request.get(api_url)
    data = await resp.json()
    
    for section in ["before_live", "live", "after_live"]:
        if section not in data: continue
        for block in data.get(section, []):
            for item in block.get("schedules", []):
                cat1 = str(item.get("category1") or "")
                cat2 = str(item.get("category2") or "")
                cat3 = str(item.get("category3") or "")
                cat = f"{cat1} > {cat2} > {cat3}"
                
                if "건강식품" in cat or "건강" in cat:
                    price = item.get("price")
                    if not price or int(price) == 0:
                        print("---")
                        print("Name:", item.get("name"))
                        print("Price (raw):", price)
                        print("discount_price:", item.get("discount_price"))
                        print("price_text:", item.get("price_text"))
                        print("sale_price:", item.get("sale_price"))
                        print("price_text_front:", item.get("price_text_front"))
                        print("mobile_price:", item.get("mobile_price"))
                        print("url:", item.get("url"))

    await scraper.close()

if __name__ == '__main__':
    asyncio.run(main())
