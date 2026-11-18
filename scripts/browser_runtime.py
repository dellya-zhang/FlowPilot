"""Chromium E2E against an isolated real FastAPI backend (no paid model)."""
import csv
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import sync_playwright

OUT=Path("browser-evidence")
OUT.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory() as tmp:
    env={**os.environ,"AI_PROVIDER":"mock","DATABASE_URL":f"sqlite+aiosqlite:///{tmp}/db.sqlite","ARTIFACT_DIR":f"{tmp}/artifacts","REDIS_URL":"","S3_ENDPOINT_URL":"","ACCESS_TOKEN":""}
    service=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8888"],env=env)
    base="http://127.0.0.1:8888"
    try:
        for _ in range(100):
            try:
                if httpx.get(base+"/api/v1/health",timeout=1).is_success:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        else:raise RuntimeError("Backend did not start")
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            context=browser.new_context(viewport={"width":1440,"height":1000},accept_downloads=True)
            page=context.new_page();errors=[]
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.goto(base+"/#marketing")
            page.click('[data-scenario="member"]')
            page.click("#create")
            page.wait_for_function("() => document.querySelectorAll('.candidate [data-action=approve]:enabled').length >= 2",timeout=30000)
            page.locator(".candidate [data-action=approve]").first.click()
            page.wait_for_function("() => !document.getElementById('finalize').disabled")
            page.click("#finalize")
            page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
            with page.expect_download() as download:
                page.locator("#artifacts button").first.click()
            with open(download.value.path(),encoding="utf-8-sig",newline="") as stream:
                rows=list(csv.DictReader(stream))
            assert rows and all(row["candidate_id"] for row in rows)
            page.screenshot(path=str(OUT/"marketing.png"),full_page=True)
            page.locator('[data-view="campaigns"]').click()
            page.wait_for_selector("#listRows tr")
            page.screenshot(path=str(OUT/"campaigns.png"))
            page.goto(base+"/support.html")
            page.wait_for_selector("#messages .assistant")
            page.click('[data-message="退款政策是什么"]')
            page.wait_for_function("() => document.querySelector('#messages').textContent.includes('人工审核')")
            page.click('[data-message="我要退款"]')
            page.wait_for_selector("#pending",state="visible")
            page.reload()
            page.wait_for_selector("#pending",state="visible")
            page.click("#no")
            page.wait_for_selector("#pending",state="hidden")
            page.wait_for_function("() => document.querySelector('#messages').textContent.includes('已取消申请')")
            page.screenshot(path=str(OUT/"support.png"))
            assert not errors,errors
            context.close()
            phone=browser.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True)
            mobile=phone.new_page();mobile.goto(base+"/support.html")
            mobile.wait_for_selector("#messages .assistant")
            mobile.screenshot(path=str(OUT/"support-mobile.png"))
            assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
            browser.close()
    finally:
        service.terminate()
        try:service.wait(timeout=8)
        except subprocess.TimeoutExpired:service.kill()
