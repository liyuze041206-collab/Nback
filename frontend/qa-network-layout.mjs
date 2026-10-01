import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {mockUploadAnalysis,selectMockUpload} from './qa-upload-fixture.mjs';
const origin=process.env.PORTAL_ORIGIN||'http://127.0.0.1:8000';
const out=resolve(import.meta.dirname,'../docs/network-layout');await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});const checks=[],errors=[];
try{
 for(const theme of ['light','dark']){
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(theme=>localStorage.setItem('gspm.preferences.v1',JSON.stringify({theme,motion:true})),theme);
  await mockUploadAnalysis(page,{windows:20});
  await page.goto(origin+'/portal.html#eeg');const eeg=page.frameLocator('#eeg-frame');await eeg.locator('#networkSvg').waitFor();
  await selectMockUpload(eeg);await eeg.locator('#startBtn').click();await eeg.locator('#pulses circle').first().waitFor();await eeg.locator('#pauseBtn').click();
  for(const width of [1440,1280,390]){
   await page.setViewportSize({width,height:width===390?844:width===1280?800:900});await page.waitForTimeout(100);
   const layout=await eeg.locator('html').evaluate(()=>{
    const box=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
    return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,stage:box('#stage'),upload:box('.compact-upload'),network:box('.network-box'),controls:box('.playback-dock'),buttons:box('.form-actions'),result:box('.right'),nodes:[...document.querySelectorAll('.node-core')].map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}})};
   });
   assert(layout.scrollWidth<=layout.width,`${theme} ${width}: no horizontal overflow`);
   assert(layout.nodes.length===24, 'All five existing node layers retained');
   assert(layout.nodes.every(n=>n.x>=layout.network.x&&n.right<=layout.network.right&&n.y>=layout.network.y&&n.bottom<=layout.network.bottom),'All network rows fit');
   assert(layout.controls.y>=layout.network.bottom-1,'Controls sit below network');
   assert(Math.abs(layout.buttons.x+layout.buttons.width/2-layout.network.x-layout.network.width/2)<1,'Control group is centered beneath network');
   if(width>=1000){
    assert(layout.stage.bottom<=layout.height+1,'Entire workspace fits first screen');
    assert(layout.upload.width<=280&&layout.upload.height<=145,'Upload stays compact');
    assert(layout.upload.x>=layout.network.right-1,'Upload does not cover network');
    assert(layout.result.y>=layout.upload.bottom,'Results sit below upload');
    assert(layout.network.width>layout.stage.width*.5,'Network is the dominant column');
   }else{
    assert(layout.controls.bottom<=layout.height,'Network and controls fit mobile first screen');
    assert(layout.network.width>=300,'Mobile network is not a shrunken desktop stage');
    assert(await eeg.locator('#startBtn').evaluate(b=>b.getBoundingClientRect().height>=40),'Mobile controls remain tappable');
   }
   await page.screenshot({path:resolve(out,`eeg-${theme}-${width}.png`)});checks.push(`${theme} ${width}`);
  }
  await eeg.locator('#resetBtn').click();assert.equal(await eeg.locator('#pulses circle').count(),0);assert.match(await eeg.locator('#matchStatus').innerText(),/已自动匹配/);
  await page.close();
 }
 const standalone=await browser.newPage({viewport:{width:1280,height:800}});await standalone.goto(origin+'/demo');
 assert(await standalone.locator('#stage').evaluate(e=>e.getBoundingClientRect().bottom<=innerHeight-28),'Standalone leaves room for attribution');
 assert.equal(await standalone.locator('.compact-upload').count(),1);assert.equal(await standalone.locator('.gspm-footer').count(),1);await standalone.close();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks,screenshots:out}));
}finally{await browser.close();}
