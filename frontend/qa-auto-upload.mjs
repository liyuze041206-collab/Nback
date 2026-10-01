import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const origin=process.env.PORTAL_ORIGIN||'http://127.0.0.1:8000';
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let uploadCount=0,releaseOld,releaseJob,oldArrived,jobArrived;
  const oldReady=new Promise(r=>oldArrived=r),jobReady=new Promise(r=>jobArrived=r);
  await page.route('**/api/recordings',route=>route.fulfill({status:201,json:{id:String(++uploadCount)}}));
  await page.route('**/api/recordings/*/prepare-analysis',async route=>{
    const id=route.request().url().split('/').at(-2);
    if(id==='1'){oldArrived();await new Promise(r=>releaseOld=r);}
    if(id==='3')return route.fulfill({status:409,json:{detail:'未找到本机支持文件，请配置 GSPM_SUPPORT_DIR。'}});
    await route.fulfill({json:{subject:id==='1'?'sub-01':'sub-05',session:'ses-S2',model_id:id==='1'?'sub-01':'sub-05',calibration_id:'cal-'+id,window_mode:'continuous',event_types:[]}});
  });
  await page.route('**/api/analyses',async route=>{
    assert.equal(route.request().postDataJSON().recording_id,'4');
    await route.fulfill({status:202,json:{id:'pending-job'}});
  });
  await page.route('**/api/analyses/pending-job',async route=>{
    jobArrived();await new Promise(r=>releaseJob=r);
    await route.fulfill({json:{status:'complete',sfreq:250,temporal_enabled:false,final_class_label:2,vote_0:0,vote_2:1,rows:[{window:1,start_sec:0,end_sec:2,prob_0:.1,prob_2:.9,class_label:2}]}});
  });
  await page.goto(origin+'/demo');await page.locator('#networkSvg').waitFor();
  const choose=name=>page.locator('#queryFile').setInputFiles({name,mimeType:'application/octet-stream',buffer:Buffer.from('UI mock; actual NPZ verified separately')});
  assert.equal(await page.locator('input[type=file]').count(),1);
  assert(await page.locator('#startBtn').isDisabled());
  await choose('old-query.npz');await oldReady;
  assert(await page.locator('#startBtn').isDisabled());assert(await page.locator('.upload-button').isEnabled());
  await choose('new-query.npz');await page.locator('#matchStatus').filter({hasText:'sub-05'}).waitFor();
  releaseOld();await page.waitForTimeout(250);
  assert.match(await page.locator('#matchStatus').innerText(),/sub-05/);
  assert.equal(await page.locator('#queryFile').evaluate(e=>e.files[0].name),'new-query.npz');
  await choose('missing-support.npz');await page.locator('#uploadMessage').filter({hasText:'未找到本机支持文件'}).waitFor();
  assert(await page.locator('#startBtn').isDisabled());
  assert.equal(await page.locator('#queryFile').evaluate(e=>e.files[0].name),'missing-support.npz');
  await choose('valid-query.npz');await page.locator('#matchStatus').filter({hasText:'已自动匹配'}).waitFor();
  await page.locator('#startBtn').click();await jobReady;
  assert(await page.locator('.upload-button').isDisabled());assert(await page.locator('#queryFile').isDisabled());
  await page.locator('#resetBtn').click();releaseJob();await page.waitForTimeout(500);
  assert(await page.locator('#startBtn').isEnabled());
  assert.equal(await page.locator('#queryFile').evaluate(e=>e.files[0].name),'valid-query.npz');
  assert.match(await page.locator('#matchStatus').innerText(),/已自动匹配/);
  assert.equal(await page.locator('#pulses circle').count(),0);assert(await page.locator('#finalDecision').isVisible());assert.equal(await page.locator('#finalClass').innerText(),'—');assert.equal(await page.locator('#finalVotes').innerText(),'等待分析结果');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,checks:['standalone single input','preparing disables start','late preparation ignored','failed preparation retains file','analysis prevents replacement','Reset retains match and ignores late result']}));
}finally{await browser.close();}
