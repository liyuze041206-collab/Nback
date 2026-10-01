import {mockUploadAnalysis,selectMockUpload} from './qa-upload-fixture.mjs';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
const origin=process.env.PORTAL_ORIGIN||'http://127.0.0.1:8000';
const output=resolve(import.meta.dirname,'../docs/brand');await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const errors=[];const checks=[];
const check=(ok,label)=>{assert.ok(ok,label);checks.push(label)};
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.__particleMoves=0;const original=SVGElement.prototype.setAttribute,prior=new WeakMap();SVGElement.prototype.setAttribute=function(name,value){if(name==='cx'&&this.parentElement?.id==='pulses'){const old=prior.get(this);if(old!==undefined&&old!==String(value))window.__particleMoves++;prior.set(this,String(value))}return original.call(this,name,value)}});
 await mockUploadAnalysis(page,{windows:20});
 await page.goto(origin+'/portal.html#architecture');const arch=page.frameLocator('#architecture-frame'),eeg=page.frameLocator('#eeg-frame');await arch.locator('.node').first().waitFor();
 const menu=async()=>{if(await page.locator('.settings-panel').isHidden())await page.locator('.settings-trigger').click()};
 const navigate=async view=>{await menu();await page.locator(`[data-page=${view}]`).click();await page.locator(`[data-panel=${view}].is-active`).waitFor();await page.waitForTimeout(280)};
 const theme=async value=>{await menu();await page.locator(`[data-theme-choice=${value}]`).click();await page.keyboard.press('Escape');await arch.locator(`html[data-theme=${value}]`).waitFor()};
 const motion=async value=>{await menu();if((await page.locator('[data-motion-toggle]').getAttribute('aria-checked'))!==String(value))await page.locator('[data-motion-toggle]').click();await page.keyboard.press('Escape');await eeg.locator(`html[data-motion=${value}]`).waitFor()};
 check(await page.locator('.gspm-footer').count()===1,'One portal attribution');check(await arch.locator('.gspm-footer,.gspm-header').count()===0,'Embedded pages omit duplicate chrome');
 await menu();await page.keyboard.press('Escape');check(await page.locator('.settings-trigger').evaluate(e=>e===document.activeElement),'Escape returns focus');await menu();await page.locator('.settings-backdrop').click({position:{x:5,y:90}});check(await page.locator('.settings-panel').isHidden(),'Outside click closes settings');
 await page.locator('.settings-trigger').focus();await page.keyboard.press('Enter');await page.keyboard.press('Tab');await page.keyboard.press('Enter');await eeg.locator('#networkSvg').waitFor();check(await page.locator('[data-panel=eeg]').evaluate(e=>e.classList.contains('is-active')),'Keyboard page switching');await navigate('architecture');
 for(const mode of ['dark','light']){
  await theme(mode);
  for(const width of [1440,1280,390]){
   await page.setViewportSize({width,height:width===390?844:1000});
   await navigate('architecture');
   if(await arch.locator('#detail').isVisible())await arch.locator('#back').click();
   check(await arch.locator('html').evaluate(e=>e.scrollWidth<=innerWidth+1),`${mode} architecture overview fits ${width}`);
   await page.screenshot({path:resolve(output,`architecture-${mode}-${width}.png`)});
   await arch.locator('.node[data-open=gsc]').click();const keys=await arch.locator('.module-nav button').evaluateAll(ns=>ns.map(n=>n.dataset.open));check(keys.length===16,'16 module details');
   for(const key of keys){await arch.locator(`.module-nav [data-open=${key}]`).click();check(await arch.locator('html').evaluate(e=>e.scrollWidth<=innerWidth+1),`${mode} ${key} fits ${width}`);check(await arch.locator('#detail-source,.visual-caption,.research-note').count()===0,`${key} source and notices removed`);if(width===1440)await page.waitForTimeout(550);if(width===1440)await page.screenshot({path:resolve(output,`detail-${mode}-${key}.png`)})}
   await arch.locator('#back').click();await navigate('eeg');check(await eeg.locator('#sourceSelect,#modelSelect,#support0File,#support2File').count()===0,'Old controls removed');
   check(await eeg.locator('html').evaluate(e=>e.scrollWidth<=innerWidth+1),`${mode} upload fits ${width}`);
   await page.screenshot({path:resolve(output,`upload-${mode}-${width}.png`)});await menu();const box=await page.locator('.settings-panel').boundingBox();check(box.x>=0&&box.x+box.width<=width,`Settings fits ${width}`);await page.keyboard.press('Escape');
  }
 }
 await page.setViewportSize({width:1440,height:1100});await selectMockUpload(eeg);await motion(true);await eeg.locator('#startBtn').click();await eeg.locator('#pulses circle').first().waitFor();
 await page.waitForFunction(()=>document.querySelector('#eeg-frame').contentWindow.__particleMoves>2);check(true,'Particles appear and move');await page.screenshot({path:resolve(output,'eeg-light-particles.png')});
 await eeg.locator('#pauseBtn').click();const held=await eeg.locator('#windowText').innerText();const frozen=await eeg.locator('#pulses circle').evaluateAll(ns=>ns.map(n=>n.getAttribute('cx')));await page.waitForTimeout(180);assert.deepEqual(await eeg.locator('#pulses circle').evaluateAll(ns=>ns.map(n=>n.getAttribute('cx'))),frozen);checks.push('Manual pause freezes particles');const wave=await eeg.locator('#eegCanvas').evaluate(c=>c.toDataURL());await page.waitForTimeout(150);check(await eeg.locator('#eegCanvas').evaluate(c=>c.toDataURL())===wave,'Manual pause freezes wave');
 await motion(false);check(await eeg.locator('#pulses circle').count()===0,'Motion off clears particles');await theme('dark');await navigate('architecture');await page.waitForTimeout(450);await navigate('eeg');check(await eeg.locator('#pauseBtn').innerText()==='继续','Theme and motion preserve manual pause');check(await eeg.locator('#windowText').innerText()===held,'View and preference changes preserve window');
 await motion(true);await eeg.locator('#pauseBtn').click();await eeg.locator('#pulses circle').first().waitFor();checks.push('Motion on and Resume continue current propagation');
 await motion(false);await page.waitForTimeout(500);const after=await eeg.locator('#windowText').innerText();check(after!==held,'Motion off continues result playback');await page.screenshot({path:resolve(output,'eeg-dark-results.png')});
 await navigate('architecture');const hidden=await eeg.locator('#windowText').innerText();await page.waitForTimeout(700);check(await eeg.locator('#windowText').innerText()===hidden,'Hidden view suspends playback');await navigate('eeg');await motion(true);await eeg.locator('#pulses circle').first().waitFor();checks.push('Returning view resumes particles');
 await eeg.locator('#resetBtn').click();await page.waitForTimeout(500);check(await eeg.locator('#pulses circle').count()===0,'Reset leaves no particle tasks');check(await eeg.locator('#phaseText').innerText()==='Idle','Reset returns Idle');
 await motion(false);await eeg.locator('#startBtn').click();await eeg.locator('#phaseText').filter({hasText:'Complete'}).waitFor({timeout:15000});check(await eeg.locator('#windowText').innerText()==='Window 20 / 20','Motion off finishes all windows');
 await eeg.locator('#startBtn').click();await eeg.locator('#windowText').filter({hasText:'Window 1 / 20'}).waitFor();await eeg.locator('#pauseBtn').click();
 for(const mode of ['dark','light']){await theme(mode);for(const width of [1440,1280,390]){await page.setViewportSize({width,height:width===390?844:1100});check(await eeg.locator('html').evaluate(e=>e.scrollWidth<=innerWidth+1),`${mode} results fit ${width}`);await page.screenshot({path:resolve(output,`results-${mode}-${width}.png`)})}}
 await eeg.locator('#resetBtn').click();await theme('light');await page.reload();await eeg.locator('html[data-theme=light][data-motion=false]').waitFor();check(true,'Saved preferences survive refresh');
 const reduced=await browser.newPage({reducedMotion:'reduce'});await reduced.goto(origin+'/architecture.html');check(await reduced.locator('html').getAttribute('data-motion')==='false','First visit follows reduced motion');await reduced.locator('.settings-trigger').click();await reduced.locator('[data-motion-toggle]').click();check(await reduced.locator('html').getAttribute('data-motion')==='true','Explicit preference overrides system reduced motion');
 const standalone=await browser.newPage();await standalone.goto(origin+'/demo');await standalone.locator('.settings-trigger').click();await standalone.locator('[data-theme-choice=light]').click();check(await standalone.locator('html').getAttribute('data-theme')==='light','Standalone shares theme controls');check(await standalone.locator('.gspm-footer').count()===1,'Standalone retains attribution');
 await eeg.locator('html').evaluate(()=>window.postMessage({type:'gspm:preferences',theme:'dark',motion:true},location.origin));await page.waitForTimeout(100);check(await eeg.locator('html').getAttribute('data-theme')==='light','Child rejects untrusted preference source');
 check(errors.length===0,'No JavaScript errors');console.log(JSON.stringify({passed:true,checks:checks.length,errors,screenshots:output},null,2));
}catch(e){console.error(e);process.exitCode=1}finally{await browser.close()}
