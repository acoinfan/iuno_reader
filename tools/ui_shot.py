"""开发用：给前端截图（不开端口）。需要 playwright + chromium。
用法：python tools/ui_shot.py <仓库的 python> <输出目录> <要添加的编号...>
页面里所有发往 http://iuno.local 的请求都交给 tools/ui_bridge.py 处理（真实的任务队列和数据）。
依次截：任务页（刚添加、进行中、全部完成）、方案页、接口页（含一次「测试翻译」）、用量页。"""
import base64
import json
import os
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

repo_py, outdir, ids = sys.argv[1], sys.argv[2], sys.argv[3:]
here = os.path.dirname(os.path.abspath(__file__))
bridge = subprocess.Popen([repo_py, os.path.join(here, "ui_bridge.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, cwd=os.path.dirname(here))


def call(method, path, body=None):
    bridge.stdin.write(json.dumps({"method": method, "path": path, "body": body}) + "\n")
    bridge.stdin.flush()
    return json.loads(bridge.stdout.readline())


def route(r):
    req = r.request
    path = req.url.split("iuno.local", 1)[1]
    body = json.loads(req.post_data) if req.post_data else None
    res = call(req.method, path, body)
    r.fulfill(status=res["code"], content_type=res["ctype"], body=base64.b64decode(res["b64"]))


def state():
    return json.loads(base64.b64decode(call("GET", "/api/state")["b64"]))


os.makedirs(outdir, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1000, "height": 900}, device_scale_factor=1)
    pg.route("http://iuno.local/**", route)
    pg.goto("http://iuno.local/")
    pg.wait_for_timeout(1500)
    pg.fill("#ids", " ".join(ids))
    pg.click("#add")
    pg.wait_for_timeout(2000)
    pg.screenshot(path=f"{outdir}/1-added.png", full_page=True)
    shot_mid = False
    for _ in range(900):  # 最多等 30 分钟
        jobs = [j for j in state()["jobs"] if j["pid"] in ids]
        bars = [b for j in jobs for b in j["progress"].get("bars", [])]
        if not shot_mid and any(0 < b["done"] for b in bars) and any(j["status"] == "running" for j in jobs):
            pg.wait_for_timeout(1600)
            pg.screenshot(path=f"{outdir}/2-running.png", full_page=True)
            shot_mid = True
        if jobs and all(j["status"] in ("done", "error", "paused") for j in jobs):
            break
        time.sleep(2)
    pg.wait_for_timeout(1600)
    pg.screenshot(path=f"{outdir}/3-done.png", full_page=True)
    print("任务状态：", [(j["pid"], j["status"], j["step"], j.get("error", "")[:200]) for j in state()["jobs"] if j["pid"] in ids])
    pg.click(".tab[data-p=presets]")
    pg.wait_for_timeout(800)
    pg.screenshot(path=f"{outdir}/4-presets.png", full_page=True)
    pg.click(".tab[data-p=api]")
    pg.wait_for_timeout(800)
    first = pg.locator("[data-tm]").first
    first.fill("deepseek-v4-pro")
    pg.locator("select[data-tt]").first.select_option("off")
    pg.locator("[data-test][data-role=translator]").first.click()
    pg.wait_for_function("document.querySelector('.res') && !document.querySelector('.res').textContent.includes('测试中')",
                         timeout=120000)
    pg.screenshot(path=f"{outdir}/5-api.png", full_page=True)
    pg.click(".tab[data-p=cost]")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{outdir}/6-cost.png", full_page=True)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.click(".tab[data-p=jobs]")
    pg.wait_for_timeout(1000)
    pg.screenshot(path=f"{outdir}/7-mobile.png", full_page=False)
    b.close()
bridge.stdin.close()
bridge.terminate()
print("截图在", outdir)
