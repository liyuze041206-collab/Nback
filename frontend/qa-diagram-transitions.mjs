import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
const origin=process.env.PORTAL_ORIGIN||'http://127.0.0.1:8000',out=resolve(import.meta.dirname,'../docs/module-diagrams');await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[],checks=[];
try{
 for(const theme of ['dark','light']){
  const page=await browser.newPage({viewport:{width:1440,height:900}});page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(theme=>localStorage.setItem('gspm.preferences.v1',JSON.stringify({theme,motion:true})),theme);
  await page.goto(origin+'/architecture.html');await page.locator('.node').first().waitFor();
  const icons=await page.locator('.flow .pulse svg').evaluateAll(ns=>ns.map(n=>n.innerHTML));assert.equal(new Set(icons).size,7,'Seven distinct overview icons');
  await page.locator('.node[data-open=raw]').click();await page.waitForTimeout(280);
  await page.locator('#detail-visual svg').evaluate(e=>window.__diagramRoot=e);
  await page.locator('[data-component=signal-axes]').evaluate(e=>window.__signalAxes=e);
  const fixed=await page.locator('#detail-visual').boundingBox();
  await page.locator('#next').click();
  assert(await page.locator('#detail-visual svg').evaluate(e=>e===window.__diagramRoot),'Persistent SVG root');
  assert(await page.locator('[data-component=signal-axes]').evaluate(e=>e===window.__signalAxes),'Shared axes stay mounted');
  assert(await page.locator('#detail-visual').evaluate(e=>e.getAnimations({subtree:true}).some(a=>a.effect.getTiming().duration===260)),'Changed components use 260ms');
  assert.equal(await page.locator('.focus-layout').evaluate(e=>getComputedStyle(e).opacity),'1','No entire-panel fade');
  const keys=await page.locator('.module-nav button').evaluateAll(ns=>ns.map(n=>n.dataset.open)),unique=[];assert.equal(keys.length,16);
  for(const key of keys){
   await page.locator(`.module-nav [data-open=${key}]`).click();await page.waitForTimeout(290);
   assert.equal(await page.locator('#detail-visual svg').getAttribute('data-module'),key);
   assert.equal(await page.locator('[data-leaving]').count(),0,'No old visual remains');
   unique.push(await page.locator('.diagram-components').innerHTML());
   const box=await page.locator('#detail-visual').boundingBox(),panel=await page.locator('.focus-visual').boundingBox();assert(Math.abs(box.width-fixed.width)<1,'Diagram frame width stays fixed');assert(Math.abs(box.y+box.height/2-panel.y-panel.height/2)<1,'Diagram is vertically centered');assert(Math.abs(box.x+box.width/2-panel.x-panel.width/2)<1,'Diagram is horizontally centered');
   const outside=await page.locator('#detail-visual svg').evaluate(svg=>[...svg.querySelectorAll('text')].filter(t=>{const b=t.getBBox();return b.x<0||b.x+b.width>565||b.y<0||b.y+b.height>345}).map(t=>t.textContent));assert.deepEqual(outside,[],`${key}: labels inside frame`);
   assert.equal(await page.locator('.visual-caption,#detail-source,.research-note').count(),0);assert.doesNotMatch(await page.locator('#detail').innerText(),/示意|非实测|参考：|论文与架构图/);
   await page.locator('.focus-visual').screenshot({path:resolve(out,`${theme}-${key}.png`)});
  }
  assert.equal(new Set(unique).size,16,'Sixteen distinct scenes');
  await page.locator('html').evaluate(()=>{for(const key of ['raw','gsc','band','joint','prototype','score','output'])document.querySelector(`.module-nav [data-open=${key}]`).click()});
  await page.waitForTimeout(300);assert.equal(await page.locator('#detail-visual svg').getAttribute('data-module'),'output');assert.equal(await page.locator('[data-leaving]').count(),0);
  await page.locator('#previous').click();assert.equal(await page.locator('#detail-visual svg').getAttribute('data-module'),'markov');
  await page.locator('html').evaluate(()=>window.GSPMUI.setPreferences({theme:document.documentElement.dataset.theme,motion:false},true));
  await page.locator('.module-nav [data-open=raw]').click();assert.equal(await page.locator('#detail-visual').evaluate(e=>e.getAnimations({subtree:true}).length),0,'Motion off immediately settles');
  for(const width of [1280,390]){await page.setViewportSize({width,height:width===390?844:720});for(const key of keys){await page.locator(`.module-nav [data-open=${key}]`).click();assert(await page.locator('html').evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Detail width fits');}await page.locator('.module-nav [data-open=preprocess]').click();await page.screenshot({path:resolve(out,`${theme}-preprocess-${width}.png`),fullPage:true});}
  checks.push(`${theme}: 7 icons, 16 centered scenes, fixed width, local transitions, rapid changes, reduced motion, 3 widths`);await page.close();
 }
 const offline=await browser.newPage();await offline.goto(new URL('../gspm_network_atlas.html',import.meta.url).href);await offline.locator('.node[data-open=raw]').click();assert.equal(await offline.locator('#detail-visual svg').getAttribute('data-module'),'raw');await offline.close();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks,screenshots:out}));
}finally{await browser.close();}
