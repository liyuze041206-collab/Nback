import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {resolve} from 'node:path';

const browser=await chromium.launch({channel:'msedge',headless:true});
const folder=resolve(import.meta.dirname,'../../真实数据测试/网页上传数据/sub-01/ses-S1');
try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8000/demo');
  await page.waitForFunction(()=>document.querySelector('#modelSelect option')?.textContent.includes('验证'));
  await page.selectOption('#sourceSelect','upload');
  for(const id of ['queryFile','support0File','support2File'])assert(await page.locator('#'+id).getAttribute('multiple')!==null);
  await page.setInputFiles('#queryFile',{name:'twoBACK.fdt',mimeType:'application/octet-stream',buffer:Buffer.from('x')});
  assert.match(await page.locator('#uploadMessage').innerText(),/FDT 不能单独/);
  await page.setInputFiles('#support0File',[
    {name:'oneBACK.set',mimeType:'application/octet-stream',buffer:Buffer.from('x')},
    {name:'oneBACK.fdt',mimeType:'application/octet-stream',buffer:Buffer.from('x')}]);
  assert.match(await page.locator('#uploadMessage').innerText(),/1-back/);
  assert.match(await page.locator('#support0Field .upload-name').innerText(),/oneBACK.set \+ oneBACK.fdt/);
  for(const [id,file] of [['queryFile','查询_2-back'],['support0File','支持_0-back'],['support2File','支持_2-back']]){
    await page.setInputFiles('#'+id,resolve(folder,`sub-01_ses-S1_${file}.npz`));
  }
  const response=page.waitForResponse(r=>r.url().endsWith('/api/analyses')&&r.request().method()==='POST');
  await page.click('#startBtn');
  const job=await(await response).json();
  await page.waitForFunction(()=>document.querySelector('#pct0').textContent.includes('%'),null,{timeout:60000});
  assert.equal(await page.locator('#finalClass').innerText(),'2-back');
  assert.match(await page.locator('#finalVotes').innerText(),/共 137 个窗口/);
  assert.equal(await page.locator('#decisionSub').innerText(),'当前 2 秒窗口 · 时序概率');
  const subBox=await page.locator('#decisionSub').boundingBox(),cardBox=await page.locator('#card0').boundingBox();
  assert(subBox.y+subBox.height<cardBox.y,'Window probability label must not overlap the first result card');
  assert.match(await page.locator('#demoNote').innerText(),/每类 7 个支持窗口/);
  assert.equal(await page.locator('#uploadMessage').isVisible(),false);
  const result=await(await page.request.get('http://127.0.0.1:8000/api/analyses/'+job.id)).json();
  assert.equal(result.status,'complete');assert.equal(result.windows,137);
  assert.equal(await page.locator('.sample-tag').innerText(),'250 Hz');
  await page.click('#pauseBtn');
  await page.screenshot({path:resolve(import.meta.dirname,'../docs/real-upload-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector('#stage').getBoundingClientRect().right<=window.innerWidth);
  await page.screenshot({path:resolve(import.meta.dirname,'../docs/real-upload-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.click('#resetBtn');
  await page.selectOption('#sourceSelect','steady');
  assert.equal(await page.locator('#uploadGrid').isVisible(),false);
  assert.equal(await page.locator('#queryFile').evaluate(el=>el.files.length),0);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',real_windows:result.windows,job_id:job.id,checks:['paired file selection','lone FDT error','1-back error','prepared uploads','real probabilities before animation','desktop/mobile','reset and mode switch']}));
}finally{await browser.close();}
