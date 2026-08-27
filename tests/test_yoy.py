import asyncio, json
from playwright.async_api import async_playwright

URL = "file:///tmp/p2-chart/docs/index.html"
SHOT_DIR = "/tmp/p2-chart/screenshots"
console_msgs=[]; page_errors=[]

async def open_drawer_for(page, year_label, gran_label, entity_name_substr, energy_label=None):
    # set year
    await page.click(f"#yearChips >> text={year_label}")
    await page.wait_for_timeout(200)
    # set granularity
    await page.click(f"#granChips >> text={gran_label}")
    await page.wait_for_timeout(200)
    if energy_label:
        await page.click(f"#energyChips >> text={energy_label}")
        await page.wait_for_timeout(200)
    # search entity
    await page.fill("#legendSearch", entity_name_substr)
    await page.wait_for_timeout(200)
    await page.click(".legend-item .name")
    await page.wait_for_timeout(400)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width":1400,"height":900})
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        await page.goto(URL, wait_until="networkidle")

        # Scenario 1: brand BYD 2026 (partial year) - all energy
        await open_drawer_for(page, "2026年", "品牌", "比亚迪", "全部")
        await page.screenshot(path=f"{SHOT_DIR}/40_drawer_byd_2026.png")
        stats1 = await page.eval_on_selector("#drawerStats", "el => el.innerText")
        print("=== BYD brand 2026 (all energy) drawer stats ===")
        print(stats1)

        # Scenario 2: brand BYD 2025 (full year)
        await page.click("#drawerClose")
        await page.wait_for_timeout(200)
        await open_drawer_for(page, "2025年", "品牌", "比亚迪", "全部")
        await page.screenshot(path=f"{SHOT_DIR}/41_drawer_byd_2025.png")
        stats2 = await page.eval_on_selector("#drawerStats", "el => el.innerText")
        print("=== BYD brand 2025 (all energy) drawer stats ===")
        print(stats2)

        # Scenario 3: 2024 year (no prior year data) - pick first entity in list
        await page.click("#drawerClose")
        await page.wait_for_timeout(200)
        await page.click("#yearChips >> text=2024年")
        await page.wait_for_timeout(200)
        await page.click("#granChips >> text=品牌")
        await page.wait_for_timeout(200)
        await page.fill("#legendSearch", "比亚迪")
        await page.wait_for_timeout(200)
        await page.click(".legend-item .name")
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"{SHOT_DIR}/42_drawer_2024_no_prior.png")
        stats3 = await page.eval_on_selector("#drawerStats", "el => el.innerText")
        print("=== BYD brand 2024 (no prior year) drawer stats ===")
        print(stats3)
        await page.click("#drawerClose")
        await page.wait_for_timeout(200)

        # Scenario 4: header caliber badge check
        badge_html = await page.eval_on_selector(".caliber-badge", "el => el.outerHTML")
        print("=== caliber badge outerHTML ===")
        print(badge_html)
        await page.screenshot(path=f"{SHOT_DIR}/43_header_badge.png")

        await browser.close()

    print("=== CONSOLE MESSAGES ===")
    for m in console_msgs: print(m)
    print("=== PAGE ERRORS ===")
    for e in page_errors: print(e)
    print("=== DONE ===")

asyncio.run(main())
