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

        # 全新加载，独立折线模式（默认），未手动碰过开关
        v0 = await page.is_checked("#otherToggle")
        print("初始(独立折线, 未手动操作过)  otherToggle checked =", v0, "(期望 False)")

        # 直接切换到堆积面积（未手动碰过开关）
        await page.click("#modeSwitch")
        await page.wait_for_timeout(300)
        v1 = await page.is_checked("#otherToggle")
        print("切到堆积面积后(未手动操作过) otherToggle checked =", v1, "(期望 True)")
        await page.screenshot(path="/tmp/p2-chart/screenshots/46_other_default_stacked.png")

        # 切回独立折线（仍未手动碰过开关）
        await page.click("#modeSwitch")
        await page.wait_for_timeout(300)
        v2 = await page.is_checked("#otherToggle")
        print("切回独立折线后(未手动操作过) otherToggle checked =", v2, "(期望 False)")
        await page.screenshot(path="/tmp/p2-chart/screenshots/47_other_default_line.png")

        # 现在手动打开一次(独立折线模式下)，再切到堆积——应保持用户选择(True)，不因为模式默认值被覆盖
        await page.click("#otherToggle")
        await page.wait_for_timeout(200)
        v3 = await page.is_checked("#otherToggle")
        print("手动勾选后(独立折线) otherToggle checked =", v3, "(期望 True)")
        await page.click("#modeSwitch")
        await page.wait_for_timeout(300)
        v4 = await page.is_checked("#otherToggle")
        print("手动勾选后再切到堆积面积 otherToggle checked =", v4, "(期望 True，且是因为尊重手动选择，不是模式默认值)")

        await browser.close()
    print("CONSOLE:", console_msgs)
    print("ERRORS:", page_errors)

asyncio.run(main())
