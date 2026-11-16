"""Browser validation against a running Mock backend; no paid model calls."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

base = os.environ.get("KNOWLEDGE_TEST_URL", "http://127.0.0.1:8041")
out = Path(__file__).resolve().parents[1] / "artifacts"
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="msedge")
    page = browser.new_page(viewport={"width":1440,"height":1100})
    errors = []
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.goto(base)
    page.locator("#knowledgeAsk").click()
    expect(page.locator("#knowledgeResult")).to_contain_text("policies.md")
    assert "人工审核" in page.locator("#knowledgeResult").inner_text()
    page.locator("#knowledgeQuestion").fill("火星天气预报")
    page.locator("#knowledgeAsk").click()
    expect(page.locator("#knowledgeResult")).to_contain_text("没有找到")
    page.locator("#knowledgeQuestion").fill("会员有什么权益")
    page.locator("#knowledgeAsk").click()
    expect(page.locator("#knowledgeResult")).to_contain_text("会员权益")
    page.locator(".knowledge-panel").screenshot(path=str(out/"knowledge-desktop.png"))
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(".knowledge-panel").screenshot(path=str(out/"knowledge-mobile.png"))
    page.goto(base+"/?demo=1")
    assert page.locator("#knowledgeAsk").is_disabled()
    assert page.locator("#knowledgeModel").is_disabled()
    assert not errors, errors
    browser.close()
print("Knowledge browser flow passed: citations, abstention, mobile and static boundary")
