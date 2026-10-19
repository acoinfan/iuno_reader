"""给渲染出的 HTML 截图，用来检查排版（开发用）。需要 playwright + chromium。
用法：python tools/screenshot.py <html 路径> <输出 png> [宽度=1400] [高度=0 表示整页] [滚动到的 CSS 选择器]"""
import sys

from playwright.sync_api import sync_playwright

src, dst = sys.argv[1], sys.argv[2]
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1400
height = int(sys.argv[4]) if len(sys.argv) > 4 else 0
anchor = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
clicks = sys.argv[6].split("||") if len(sys.argv) > 6 and sys.argv[6] else []  # 截图前依次点击的元素

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": width, "height": height or 1000}, device_scale_factor=1)
    pg.goto("file://" + src, wait_until="load", timeout=90000)
    pg.wait_for_timeout(4000)  # 等 MathJax 和网页字体
    fonts = pg.evaluate("document.fonts.check('16px \"CMU Serif\"')")
    print("CMU Serif 已加载：", fonts)
    for c in clicks:
        pg.locator(c).first.click()
        pg.wait_for_timeout(400)
        print("已点击", c)
    if clicks:
        print("高亮的元素数：", pg.locator(".f1,.f2,.f3").count(), "；其中中文栏：", pg.locator(".zh .f1,.zh .f2,.zh .f3").count())
    if anchor:
        pg.locator(anchor).first.scroll_into_view_if_needed()
        pg.wait_for_timeout(500)
        pg.screenshot(path=dst, full_page=False)
    else:
        pg.screenshot(path=dst, full_page=(height == 0))
    b.close()
print("截图", dst)
