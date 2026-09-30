import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const origin='http://127.0.0.1:5173';
const demo=await readFile(resolve(import.meta.dirname,'../gspm_eeg_workload_demo.html'),'utf8');
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.setDefaultTimeout(12000);
  await page.addInitScript(()=>{
    window.__particleMoves=0;
    const original=SVGElement.prototype.setAttribute;
    const prior=new WeakMap();
    SVGElement.prototype.setAttribute=function(name,value){
      if(name==='cx'&&this.parentElement?.id==='pulses'){
        const old=prior.get(this);
        if(old!==undefined&&old!==String(value))window.__particleMoves++;
        prior.set(this,String(value));
      }
      return original.call(this,name,value);
    };
  });
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/demo')return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:demo});
    if(url.pathname==='/api/status')return route.fulfill({json:{model:{folds:[{id:'sub-01',ready:true,validation_subject:'sub-02'}]}}});
    if(url.pathname==='/api/demo/analyses'&&route.request().method()==='POST')return route.fulfill({json:{id:'ui-particle-test'}});
    if(url.pathname==='/api/demo/analyses/ui-particle-test')return route.fulfill({json:{status:'complete',mode:'demo-real',temporal_enabled:false,sfreq:250,final_class_label:0,vote_0:2,vote_2:0,rows:[0,1].map(i=>({window:i+1,start_sec:i*2,end_sec:i*2+2,prob_0:.8,prob_2:.2,class_label:0}))}});
    return route.continue();
  });
  await page.goto(`${origin}/portal.html#eeg`);
  const eeg=page.frameLocator('#eeg-frame');
  await eeg.locator('#networkSvg').waitFor();
  await eeg.locator('#startBtn').click();
  await eeg.locator('#pulses circle').first().waitFor();
  await page.waitForFunction(()=>{const f=document.querySelector('#eeg-frame')?.contentWindow;return (f?.__particleMoves||0)>=2});
  const moves=await page.locator('#eeg-frame').evaluate(f=>f.contentWindow.__particleMoves);
  const screenshot=resolve(import.meta.dirname,'../docs/portal/eeg-particles-active.png');await mkdir(resolve(import.meta.dirname,'../docs/portal'),{recursive:true});await page.screenshot({path:screenshot});
  await eeg.locator('#pauseBtn').click();
  const frozen=await eeg.locator('#pulses circle').evaluateAll(circles=>circles.map(c=>[c.getAttribute('cx'),c.getAttribute('cy')]));
  await page.waitForTimeout(180);
  assert.deepEqual(await eeg.locator('#pulses circle').evaluateAll(circles=>circles.map(c=>[c.getAttribute('cx'),c.getAttribute('cy')])),frozen,'Pause freezes flowing particles');
  const pausedMoves=await page.locator('#eeg-frame').evaluate(f=>f.contentWindow.__particleMoves);
  await eeg.locator('#pauseBtn').click();
  await page.waitForFunction(n=>{const f=document.querySelector('#eeg-frame')?.contentWindow;return (f?.__particleMoves||0)>n},pausedMoves);
  await eeg.locator('#resetBtn').click();
  await page.waitForFunction(()=>{const f=document.querySelector('#eeg-frame')?.contentDocument;return f?.querySelector('#phaseText')?.textContent==='Idle'&&f?.querySelectorAll('#pulses circle').length===0});
  console.log(JSON.stringify({passed:true,particleMoves:moves,screenshot,checks:['particles appear and move in the portal','Pause freezes particles','Resume continues animation','Reset clears particles']}));
}catch(error){console.error(error);process.exitCode=1}
finally{await browser.close()}
