import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
const out=resolve(import.meta.dirname,'../docs/refinement');await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
try{
 for(const theme of ['dark','light']){
  const page=await browser.newPage({viewport:{width:1440,height:1100}});page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(mode=>localStorage.setItem('gspm.preferences.v1',JSON.stringify({theme:mode,motion:true})),theme);
  await page.route('**/api/demo/analyses',r=>r.fulfill({json:{id:'top-row'}}));
  await page.route('**/api/demo/analyses/top-row',r=>r.fulfill({json:{status:'complete',mode:'demo-real',temporal_enabled:false,sfreq:250,final_class_label:0,vote_0:1,vote_2:0,rows:[{window:1,start_sec:0,end_sec:2,prob_0:.8,prob_2:.2,class_label:0}]}}));
  await page.goto('http://127.0.0.1:8000/portal.html#architecture');const arch=page.frameLocator('#architecture-frame'),eeg=page.frameLocator('#eeg-frame');await arch.locator('.node').first().waitFor();
  assert.equal(await page.locator('.gspm-brand span').innerText(),'思维轨迹');
  for(const width of [1440,1280,390]){
   await page.setViewportSize({width,height:width===390?844:1100});
   const centered=await page.locator('.settings-trigger').evaluate(b=>{const r=b.getBoundingClientRect(),s=b.querySelector('svg').getBoundingClientRect();return Math.abs(s.x+s.width/2-r.x-r.width/2)<.1&&Math.abs(s.y+s.height/2-r.y-r.height/2)<.1});assert(centered,`Centered settings icon at ${width}`);
   assert.equal(await arch.locator(`.section-letter .badge-${theme}:visible`).count(),4);
   for(const letter of ['A','B','C','D'])assert(await arch.locator(`.section-letter img[src$="letter-${letter}-${theme}-v2.png"]`).evaluate(i=>i.complete&&i.naturalWidth>0),`Loaded ${theme} ${letter}`);
   assert(await arch.locator('html').evaluate(e=>e.scrollWidth<=innerWidth+1));await page.screenshot({path:resolve(out,`architecture-${theme}-${width}.png`)});
  }
  await page.setViewportSize({width:1440,height:1100});await page.locator('.settings-trigger').click();await page.locator('[data-page=eeg]').click();await eeg.locator('#networkSvg').waitFor();await page.waitForTimeout(280);
  await eeg.locator('#startBtn').click();const first=eeg.locator('#pulses circle[data-from=L0N0][data-to=L1N0]');await first.waitFor();const x=Number(await first.getAttribute('cx'));await page.waitForTimeout(65);assert(Number(await first.getAttribute('cx'))>x,'First-row particle moves on the horizontal connection');assert.equal(Number(await first.getAttribute('cy')),90);
  await eeg.locator('#pauseBtn').click();const held=await first.getAttribute('cx');await page.waitForTimeout(300);assert.equal(await first.getAttribute('cx'),held,'Pause freezes first-row particle');
  const network=resolve(out,`network-${theme}.png`);await eeg.locator('#networkSvg').screenshot({path:network});await page.screenshot({path:resolve(out,`eeg-${theme}.png`)});
  if(theme==='dark'){
   const pixels=spawnSync('python',['-c',"from PIL import Image;import sys;im=Image.open(sys.argv[1]).convert('RGB');b=sum(im.getpixel((x,90))[2] for x in range(170,210))/40;assert b>60,f'horizontal edge is missing: {b}';print('Horizontal edge blue intensity:',round(b,1))",network],{encoding:'utf8'});assert.equal(pixels.status,0,pixels.stderr);console.log(pixels.stdout.trim());
  }
  await eeg.locator('#pauseBtn').click();await eeg.locator('#resetBtn').click();await page.waitForTimeout(300);assert.equal(await eeg.locator('#pulses circle').count(),0);
  await page.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['team identity','centered icon at 3 widths','eight theme-specific PNG badges','first-row particle movement and pause','visible horizontal edge pixels','Reset','no overflow or script errors'],screenshots:out},null,2));
}finally{await browser.close()}
