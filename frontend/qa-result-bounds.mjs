import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {mockUploadAnalysis,selectMockUpload} from './qa-upload-fixture.mjs';
const origin=process.env.PORTAL_ORIGIN||'http://127.0.0.1:8000',out=resolve(import.meta.dirname,'../docs/result-bounds');await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[],checks=[];
async function fit(scope,label){
 const bad=await scope.locator('html').evaluate(()=>{
  const issues=[],right=document.querySelector('.right').getBoundingClientRect(),dock=document.querySelector('.playback-dock').getBoundingClientRect(),stage=document.querySelector('#stage').getBoundingClientRect();
  const within=(r,p)=>r.x>=p.x-1&&r.right<=p.right+1&&r.y>=p.y-1&&r.bottom<=p.bottom+1;
  for(const el of document.querySelectorAll('.right .result-card,.right .final-decision,.right .final-votes')){
   if(!el.checkVisibility())continue;if(!within(el.getBoundingClientRect(),right))issues.push(el.className);
  }
  for(const [selector,parent] of [['.right',right],['#statusCard',dock]]){
   const walker=document.createTreeWalker(document.querySelector(selector),NodeFilter.SHOW_TEXT);let node;
   while(node=walker.nextNode()){if(!node.textContent.trim()||!node.parentElement.checkVisibility())continue;const range=document.createRange();range.selectNodeContents(node);for(const r of range.getClientRects())if(!within(r,parent))issues.push('text:'+node.textContent);}
  }
  for(const selector of ['#statusCard','#statusTitle','#statusDetail']){const e=document.querySelector(selector);if(e.checkVisibility()&&!within(e.getBoundingClientRect(),dock))issues.push(selector);}
  if(!within(right,stage))issues.push('result container');
  if(document.documentElement.scrollWidth>innerWidth)issues.push('horizontal overflow');
  return issues;
 });assert.deepEqual(bad,[],label);
}
try{
 for(const theme of ['dark','light']){
  const page=await browser.newPage({viewport:{width:1280,height:720}});page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(theme=>localStorage.setItem('gspm.preferences.v1',JSON.stringify({theme,motion:true})),theme);await mockUploadAnalysis(page,{windows:138});
  await page.goto(origin+'/portal.html#eeg');const eeg=page.frameLocator('#eeg-frame');await eeg.locator('#networkSvg').waitFor();
  await fit(eeg,'Waiting');await selectMockUpload(eeg);await fit(eeg,'Prepared');
  await eeg.locator('#startBtn').click();await eeg.locator('#pulses circle').first().waitFor();assert.equal(await eeg.locator('#statusTitle').innerText(),'播放中');assert.equal(await eeg.locator('.right #statusCard').count(),0);
  for(const [width,height] of [[1440,900],[1280,720],[1024,576],[853,480],[390,844]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(60);await fit(eeg,`${theme} running ${width}×${height}`);
   if(width===1280)await page.screenshot({path:resolve(out,`${theme}-running-1280x720.png`)});
   await eeg.locator('#pauseBtn').click();await fit(eeg,`${theme} paused ${width}`);await eeg.locator('#pauseBtn').click();
  }
  await page.setViewportSize({width:1280,height:720});await eeg.locator('#resetBtn').click();
  await page.locator('html').evaluate(()=>window.GSPMUI.setPreferences({theme:document.documentElement.dataset.theme,motion:false},true));
  await eeg.locator('html[data-motion=false]').waitFor();await eeg.locator('#startBtn').click();await eeg.locator('#phaseText').filter({hasText:'Complete'}).waitFor({timeout:70000});
  assert.equal(await eeg.locator('#statusTitle').innerText(),'分析完成');await fit(eeg,`${theme} complete with 138 windows`);
  await eeg.locator('#resetBtn').click();
  const detail='缺少兼容的个人校准支持数据。'+ '请检查被试、session、模型版本与来源是否一致，并配置本机支持目录。'.repeat(12);
  await page.route('**/api/recordings/*/prepare-analysis',route=>route.fulfill({status:409,json:{detail}}));
  await eeg.locator('#queryFile').setInputFiles({name:'missing-support.npz',mimeType:'application/octet-stream',buffer:Buffer.from('UI mock')});
  await eeg.locator('#uploadMessage').filter({hasText:detail}).waitFor();assert.equal(await eeg.locator('#uploadMessage').innerText(),detail);assert(await eeg.locator('#startBtn').isDisabled());
  assert(await eeg.locator('#uploadMessage').evaluate(e=>e.scrollHeight<=e.clientHeight+1),'Long errors are not clipped or internally scrolled');await fit(eeg,`${theme} long error`);
  await page.close();checks.push(`${theme}: waiting, playing, paused, 138-window completion, long errors, narrow/short/zoom-equivalent viewports`);
 }
 for(const theme of ['dark','light']){
  const standalone=await browser.newPage({viewport:{width:1280,height:720}});
  await standalone.addInitScript(theme=>localStorage.setItem('gspm.preferences.v1',JSON.stringify({theme,motion:true})),theme);
  await mockUploadAnalysis(standalone,{windows:2});await standalone.goto(origin+'/demo');await fit(standalone,`Standalone ${theme} waiting`);
  await selectMockUpload(standalone);await standalone.locator('#startBtn').click();await standalone.locator('#pulses circle').first().waitFor();await fit(standalone,`Standalone ${theme} playing`);
  await standalone.locator('#pauseBtn').click();await fit(standalone,`Standalone ${theme} paused`);await standalone.locator('#pauseBtn').click();
  await standalone.locator('html').evaluate(()=>window.GSPMUI.setPreferences({theme:document.documentElement.dataset.theme,motion:false},true));
  await standalone.locator('#phaseText').filter({hasText:'Complete'}).waitFor();await fit(standalone,`Standalone ${theme} complete`);
  await standalone.locator('#resetBtn').click();
  const analysisError='模型分析失败：上传记录与当前校准的预处理配置不一致。请重新准备该记录并检查校准来源。'.repeat(5);
  await standalone.route('**/api/analyses/upload-ui-test',route=>route.fulfill({json:{status:'failed',error:analysisError}}));
  await standalone.locator('#startBtn').click();await standalone.locator('#uploadMessage').filter({hasText:analysisError}).waitFor();
  assert.equal(await standalone.locator('#uploadMessage').innerText(),analysisError);assert(await standalone.locator('#uploadMessage').evaluate(e=>e.scrollHeight<=e.clientHeight+1));
  await fit(standalone,`Standalone ${theme} long analysis error`);await standalone.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks,screenshots:out}));
}finally{await browser.close();}
