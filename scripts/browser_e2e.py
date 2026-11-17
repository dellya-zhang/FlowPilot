from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]/'docs';root.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True, channel="msedge")
 page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
 errors=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 page.goto('http://127.0.0.1:8017')
 page.locator('[data-view="marketing"]').click()
 page.click('[data-scenario="experiment"]')
 assert page.input_value('#audience') == 'dormant'
 page.click('#create')
 page.wait_for_selector('.candidate',timeout=20000)
 page.wait_for_function("() => document.querySelectorAll('.candidate').length===3")
 assert page.locator('.strategy-preview').count()==3
 page.locator('.strategy-preview details').first.locator('summary').first.click()
 assert '权益依据' in page.locator('.strategy-preview').first.inner_text()
 page.click('#experiment')
 page.wait_for_selector('#experimentView button')
 assert page.locator('.candidate [data-action=approve]:enabled').count() == 0
 page.get_by_role('button',name='记录一名模拟转化').first.click()
 page.get_by_role('button',name='人工选为胜出方案').first.click()
 page.wait_for_function("() => !document.getElementById('finalize').disabled")
 page.click('#finalize')
 page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
 page.locator('[data-view="support"]').click()
 page.click('#send')
 page.wait_for_function("() => document.getElementById('chatlog').textContent.includes('退款需')")
 page.fill('#message','我要退款');page.click('#send')
 page.wait_for_selector('#confirmBox:visible');page.click('#confirm')
 page.wait_for_function("() => document.getElementById('chatlog').textContent.includes('已记录人工审核')")
 page.locator('[data-view="marketing"]').click()
 page.screenshot(path=str(root/'工作台预览.png'),full_page=True)
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('({view:document.body.dataset.view,w:innerWidth,sw:document.documentElement.scrollWidth,wide:[...document.querySelectorAll("body *")].filter(x=>x.getBoundingClientRect().right>innerWidth).map(x=>x.tagName+"."+x.className).slice(0,15)})')
 page.screenshot(path=str(root/'移动端预览.png'),full_page=True)
 for view in ['support','knowledge','settings','marketing']:
  page.locator('[data-view="'+view+'"]').click()
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('({view:document.body.dataset.view,w:innerWidth,sw:document.documentElement.scrollWidth,wide:[...document.querySelectorAll("body *")].filter(x=>x.getBoundingClientRect().right>innerWidth).map(x=>x.tagName+"."+x.className).slice(0,15)})')
 page.click('[data-scenario="member"]')
 page.click('#create')
 page.wait_for_function("() => document.querySelectorAll('.candidate [data-action=approve]:enabled').length===3")
 page.locator('.candidate [data-action="approve"]').first.click()
 page.click('#pilot')
 page.wait_for_function("() => !document.getElementById('finalize').disabled")
 page.click('#finalize')
 page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
 with page.expect_download() as downloaded:
  page.locator('#artifacts button').first.click()
 assert downloaded.value.suggested_filename.endswith('.csv')
 assert not errors,errors
 print('Browser E2E passed: generation, AB feedback, promotion, full export, FAQ, confirmation, mobile layout; zero JS errors')
 browser.close()

