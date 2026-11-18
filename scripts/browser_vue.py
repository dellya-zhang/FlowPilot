"""Browser acceptance of the compiled Vue console with real SQL and offline model."""
import os,sys,tempfile,time,subprocess
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
out=Path("vue-evidence");out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    env={**os.environ,"AI_PROVIDER":"mock","DATABASE_URL":f"sqlite+aiosqlite:///{tmp}/db.sqlite","ARTIFACT_DIR":f"{tmp}/artifacts","ACCESS_TOKEN":"","REDIS_URL":"","S3_ENDPOINT_URL":""}
    service=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8889"],env=env)
    base="http://127.0.0.1:8889"
    try:
        for _ in range(100):
            try:
                if httpx.get(base+"/api/v1/health",timeout=1).is_success:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        else:raise RuntimeError("Backend unavailable")
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={"width":1440,"height":1000},accept_downloads=True);errors=[]
            page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(base+"/console/#campaigns")
            page.get_by_role("button",name="填入示例需求").click()
            page.get_by_role("button",name="创建并生成候选").click()
            page.get_by_role("button",name="审批",exact=True).first.wait_for(timeout=30000)
            page.screenshot(path=str(out/"plan.png"),full_page=True)
            page.get_by_role("button",name="审批",exact=True).first.click()
            page.get_by_role("button",name="全量交付").click()
            page.get_by_role("button",name="下载 CSV").wait_for(timeout=15000)
            with page.expect_download() as d:page.get_by_role("button",name="下载 CSV").click()
            assert "customer_id" in Path(d.value.path()).read_text("utf-8-sig")
            page.get_by_role("button",name="客服工作台").click()
            page.get_by_role("button",name="我的订单").click()
            page.wait_for_function("document.querySelector('.chat').innerText.includes('FP-0001-A')")
            page.get_by_placeholder("请输入问题").fill("我要退款 FP-0001-A")
            page.get_by_role("button",name="发送").click()
            page.get_by_role("button",name="确认",exact=True).click()
            page.wait_for_function("document.body.innerText.includes('PENDING_REVIEW')")
            page.screenshot(path=str(out/"support.png"),full_page=True)
            page.get_by_role("button",name="活动管理").click()
            page.screenshot(path=str(out/"campaigns.png"))
            assert not errors,errors
            browser.close()
    finally:
        service.terminate()
        try:service.wait(timeout=8)
        except subprocess.TimeoutExpired:service.kill()
