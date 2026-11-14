from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]/'docs';root.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True, channel="msedge")
 page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
 errors=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 page.goto('http://127.0.0.1:8018')
 page.click('#create')
 page.wait_for_selector('.candidate',timeout=20000)
 page.wait_for_function("() => document.querySelectorAll('.candidate').length===3")
 page.click('#experiment')
 page.wait_for_selector('#experimentView button')
 page.get_by_role('button',name='记录一名模拟转化').first.click()
 page.get_by_role('button',name='人工选为胜出方案').first.click()
 page.wait_for_function("() => !document.getElementById('finalize').disabled")
 page.click('#finalize')
 page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
 page.click('#send')
 page.wait_for_function("() => document.getElementById('chatlog').textContent.includes('退款需')")
 page.fill('#message','我要退款');page.click('#send')
 page.wait_for_selector('#confirmBox:visible');page.click('#confirm')
 page.wait_for_function("() => document.getElementById('chatlog').textContent.includes('已记录人工审核')")
 page.screenshot(path=str(root/'DeepSeek工作台预览.png'),full_page=True)
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
 page.screenshot(path=str(root/'DeepSeek移动端预览.png'),full_page=True)
 assert not errors,errors
 print('Browser E2E passed: generation, AB feedback, promotion, full export, FAQ, confirmation, mobile layout; zero JS errors')
 browser.close()

