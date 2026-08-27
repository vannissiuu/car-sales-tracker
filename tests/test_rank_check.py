import asyncio
from playwright.async_api import async_playwright

URL = "file:///tmp/p2-chart/docs/index.html"
console_msgs=[]; page_errors=[]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width":1400,"height":900})
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        await page.goto(URL, wait_until="networkidle")

        await page.click("#yearChips >> text=2026年")
        await page.wait_for_timeout(200)
        await page.click("#granChips >> text=品牌")
        await page.wait_for_timeout(200)
        await page.fill("#legendSearch", "小鹏")
        await page.wait_for_timeout(300)
        await page.click(".legend-item .name")
        await page.wait_for_timeout(400)
        stats = await page.eval_on_selector("#drawerStats", "el => el.innerText")
        print("=== 小鹏 brand 2026 drawer stats (expect rank 19, prev-period rank 18, down 1) ===")
        print(stats)
        await page.screenshot(path="/tmp/p2-chart/screenshots/45_drawer_rank_check.png")
        await browser.close()
    print("CONSOLE:", console_msgs)
    print("ERRORS:", page_errors)

asyncio.run(main())
