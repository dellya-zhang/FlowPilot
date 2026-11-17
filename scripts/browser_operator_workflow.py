"""Full operator journey on both browser simulation and real Python Mock API."""
import csv
import json
from pathlib import Path
from browser_planning import run_checks
ROOT=Path(__file__).resolve().parents[1]


def check(browser,url,label):
    context=browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True)
    page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(url)
    page.locator('[data-view="marketing"]').click()
    page.click('[data-scenario="member"]')
    page.locator('.planning-fields>summary').click()
    page.select_option('#comparisonAxis','frequency')
    page.fill('#referenceMessage','欢迎查询会员积分或联系专属客服。')
    page.fill('#unitCost','0.2')
    page.click('#create')
    page.wait_for_function("() => document.querySelectorAll('.candidate [data-action=approve]:enabled').length===3")
    assert '1次触达' in page.locator('.candidate-comparison').inner_text()
    assert '3次触达' in page.locator('.candidate-comparison').inner_text()
    for index in [0,1]:
        editor=page.locator('.revision-editor').nth(index)
        editor.locator('summary').click()
        editor.locator('textarea').nth(0).fill('权益以活动说明为准，可查询积分或联系专属客服。')
        editor.locator('textarea').nth(1).fill('减少重复措辞，统一两臂的确认文案')
        editor.get_by_role('button',name='保存为 v2').click()
        page.wait_for_function('(i) => document.querySelectorAll(".candidate .badge")[i].textContent.includes("v2")',arg=index)
    history=page.locator('.revision-history').first
    history.locator('summary').click()
    page.wait_for_selector('.revision-entry')
    assert history.locator('.revision-entry').count()==2
    history.locator('summary').click()
    review=page.locator('.review-editor').first
    review.locator('summary').click()
    review.locator('select').nth(0).select_option('edited')
    review.locator('input[type=number]').nth(1).fill('12')
    review.locator('input[type=number]').nth(2).fill('10')
    review.locator('textarea').fill('需要改写开头，修改后可以用于试点')
    review.get_by_role('button',name='保存方案评价').click()
    page.wait_for_function("() => document.getElementById('evaluationSummary').textContent.includes('耗时差 -2')")
    page.click('#experiment')
    page.wait_for_selector('.experiment-arm')
    page.get_by_role('button',name='人工选为胜出方案').first.click()
    page.wait_for_function("() => !document.getElementById('finalize').disabled")
    page.click('#finalize')
    page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
    with page.expect_download() as downloaded:
        page.locator('#artifacts button').first.click()
    with open(downloaded.value.path(),'r',encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    assert rows and all(r['plan_revision']=='2' and r['contacts_per_user']=='1' and r['message']=='权益以活动说明为准，可查询积分或联系专属客服。' for r in rows)
    for outcome in ['delivered','converted']:
        page.locator('#observationEditor>summary').click()
        page.select_option('#observationOutcome',outcome)
        if outcome=='delivered':page.fill('#observationCost','0.2')
        page.click('#saveObservation')
        page.wait_for_function("() => !document.getElementById('observationEditor').open")
    page.wait_for_function("() => document.getElementById('evaluationSummary').textContent.includes('窗口内转化 1')")
    assert '观察窗口未全部结束' in page.locator('#evaluationSummary').inner_text()
    page.locator('#observationEditor>summary').click()
    page.locator('.batch-observations>summary').click()
    options=page.locator('#observationUser option').evaluate_all('(nodes)=>nodes.map(x=>Number(x.value))')
    from datetime import datetime,timezone
    batch=[{'event_id':'browser-batch-'+label,'customer_id':options[1],'outcome':'delivered','occurred_at':datetime.now(timezone.utc).isoformat(),'contact_number':1,'actual_cost_cny':.2},{'event_id':'browser-invalid-'+label,'customer_id':999999,'outcome':'delivered','occurred_at':datetime.now(timezone.utc).isoformat(),'contact_number':1}]
    page.fill('#observationBatch',json.dumps(batch))
    page.click('#saveObservationBatch')
    page.wait_for_function("() => document.getElementById('observationBatchResult').textContent.includes('拒绝 1')")
    with page.expect_download() as downloaded:
        page.click('#downloadEvaluation')
    report=json.loads(Path(downloaded.value.path()).read_text('utf-8'))
    assert report['decision']['revision']==2
    demo=next(g for g in report['groups'] if g['source']=='demo')
    assert demo['review_count']==1 and demo['observed_delivered']==2 and demo['within_window_converted']==1
    assert demo['reported_minutes_difference']==-2
    assert next(g for g in report['groups'] if g['source']=='manual_observation')['review_count']==0
    if label=='backend':
        page.locator('.evaluation-panel').screenshot(path=str(ROOT/'docs/运营复盘桌面.png'))
        page.locator('.revision-history').first.locator('summary').click()
        page.wait_for_selector('.revision-entry')
        page.locator('.candidate').first.screenshot(path=str(ROOT/'docs/运营版本桌面.png'))
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if label=='backend':page.locator('.evaluation-panel').screenshot(path=str(ROOT/'docs/运营复盘手机.png'))
    for view in ['support','knowledge','settings','marketing']:
        page.locator('nav [data-view="'+view+'"]').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors,errors
    print(label+': controlled frequency comparison, two revisions, history, review, frozen CSV, delivery/conversion and JSON report passed')
    context.close()


if __name__=='__main__':run_checks(check)
