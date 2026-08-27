import asyncio
from playwright.async_api import async_playwright

URL = "file:///tmp/p2-chart/docs/index.html"
SHOT_DIR = "/tmp/p2-chart/screenshots"
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
        await page.click("#granChips >> text=车体类型 → 车型")
        await page.wait_for_timeout(200)
        await page.select_option("#bodyTypeSelect", label="SUV")
        await page.wait_for_timeout(200)
        await page.fill("#legendSearch", "零跑A10")
        await page.wait_for_timeout(300)
        await page.click(".legend-item .name")
        await page.wait_for_timeout(400)
        stats = await page.eval_on_selector("#drawerStats", "el => el.innerText")
        print("=== 零跑A10 (SUV, 2026) drawer stats (new model, no prior-year data) ===")
        print(stats)
        await page.screenshot(path=f"{SHOT_DIR}/44_drawer_new_model.png")
        await browser.close()

    print("=== CONSOLE MESSAGES ===")
    for m in console_msgs: print(m)
    print("=== PAGE ERRORS ===")
    for e in page_errors: print(e)

asyncio.run(main())
