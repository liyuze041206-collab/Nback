import {chromium} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const base=process.env.ATLAS_ORIGIN||'http://127.0.0.1:8000';
const output=new URL('../docs/architecture/',import.meta.url);
await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const failures=[];const check=(ok,message)=>{if(!ok)failures.push(message)};
try{
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 page.on('pageerror',e=>failures.push(e.message));
 await page.goto(base+'/architecture.html');
 await page.locator('.node').first().waitFor();
 check(await page.locator('.flow img,canvas').count()===0,'Diagram must use native elements, not bitmap/canvas');
 check(await page.locator('.node').count()===7,'Seven overview nodes');
 await page.screenshot({path:fileURLToPath(new URL('overview-desktop.png',output)),fullPage:true,animations:"disabled"});
 await page.locator('.settings-trigger').click();await page.locator('[data-motion-toggle]').click();await page.keyboard.press('Escape');
 check(await page.locator('body').evaluate(e=>e.classList.contains('paused')),'Pause animation');
 await page.locator('.node[data-open=gsc]').click();
 await page.getByRole('heading',{name:'GSC 门控编码器',exact:true}).waitFor();
 await page.screenshot({path:fileURLToPath(new URL('gsc-desktop.png',output)),fullPage:true,animations:"disabled"});
 const keys=await page.locator('.module-nav button').evaluateAll(ns=>ns.map(n=>n.dataset.open));
 for(const key of keys){await page.locator(`.module-nav [data-open=${key}]`).click();await page.waitForFunction(k=>location.hash==='#'+k,key);check(await page.locator('#detail-title').innerText()!=='','Title '+key);check(await page.locator('#detail-visual svg').count()===1,'Diagram '+key);check(await page.locator('#detail-source,.visual-caption,.research-note').count()===0,'No source or explanatory notices '+key);}
 await page.keyboard.press('Escape');await page.locator('#overview').waitFor();
 await page.locator('.node[data-open=psd]').focus();await page.keyboard.press('Enter');await page.getByRole('heading',{name:'Welch 对数功率谱',exact:true}).waitFor();
 await page.goBack();await page.locator('#overview').waitFor();
 check(await page.locator('.lab-link').getAttribute('href')==='/demo','Classification link');
 await page.locator('.settings-trigger').click();await page.locator('[data-page=eeg]').click();await page.frameLocator('#eeg-frame').locator('#networkSvg').waitFor();await page.locator('.settings-trigger').click();await page.locator('[data-page=architecture]').click();await page.frameLocator('#architecture-frame').locator('.node').first().waitFor();
 for(const width of [390,768,1024]){
  await page.setViewportSize({width,height:844});await page.goto(base+'/architecture.html');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overview overflow '+width);
  if(width===390)await page.screenshot({path:fileURLToPath(new URL('overview-mobile.png',output)),fullPage:true,animations:"disabled"});
  await page.locator('[data-open=prototype]').first().click();await page.locator('#detail').waitFor();
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Detail overflow '+width);
  if(width===390)await page.screenshot({path:fileURLToPath(new URL('prototype-mobile.png',output)),fullPage:true,animations:"disabled"});
 }
 const reduced=await browser.newPage({reducedMotion:'reduce'});await reduced.goto(base+'/architecture.html');check((await reduced.locator('html').getAttribute('data-motion'))==='false','Reduced motion');
 const offline=await browser.newPage();await offline.goto(new URL('../gspm_network_atlas.html',import.meta.url).href);await offline.locator('.node').first().waitFor();await offline.locator('.node[data-open=gsc]').click();await offline.getByRole('heading',{name:'GSC 门控编码器',exact:true}).waitFor();check(await offline.locator('.lab-link').getAttribute('href')==='gspm_eeg_workload_demo.html','Standalone relative link');
 console.log(JSON.stringify({passed:failures.length===0,modules:keys.length,failures,screenshots:fileURLToPath(output)},null,2));if(failures.length)process.exitCode=1;
}finally{await browser.close()}
