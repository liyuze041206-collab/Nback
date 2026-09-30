import {chromium} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const origin='http://127.0.0.1:8000';const output=new URL('../docs/',import.meta.url);await mkdir(fileURLToPath(output),{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
const failures=[];const require=(ok,msg)=>{if(!ok)failures.push(msg)};
async function visit(page,url){await page.goto(origin+url);await page.waitForLoadState('networkidle');}
try{
 const desktop=await browser.newPage({viewport:{width:1440,height:900},deviceScaleFactor:1});desktop.on('pageerror',e=>failures.push('JS: '+e.message));
 await visit(desktop,'/');await desktop.screenshot({path:fileURLToPath(new URL('home-desktop.png',output)),fullPage:true});
 require(await desktop.locator('canvas').count()===2,'首页双 3D 场景');
 await desktop.getByRole('button',{name:'合拢网络'}).click();require((await desktop.getByRole('button',{name:'拆解网络'}).count())===1,'拆解/合拢');
 await desktop.getByRole('tab',{name:'自监督预训练'}).click();require(await desktop.getByRole('button',{name:/掩码重建/}).count()>0,'预训练分支');
 await desktop.getByRole('tab',{name:'目标推理'}).click();await desktop.getByRole('button',{name:/深入 GSC/}).click();require(await desktop.getByRole('button',{name:/频带门控/}).count()>0,'GSC 内部分解');
 await desktop.getByRole('button',{name:/返回完整网络/}).click();await desktop.getByRole('switch',{name:'二维视图'}).click();require(await desktop.locator('.flat-network').count()===1,'二维模式');
 await desktop.getByRole('switch',{name:'二维视图'}).click();await desktop.getByRole('button',{name:'复位视角'}).click();
 await visit(desktop,'/lab');await desktop.getByRole('button',{name:'运行演示'}).click();await desktop.getByText('以下为预设演示结果，不是网络实际预测。').waitFor();await desktop.screenshot({path:fileURLToPath(new URL('lab-desktop.png',output)),fullPage:true});
 require(await desktop.locator('.state-timeline>div').count()===30,'演示时间线');
 await desktop.getByRole('tab',{name:'本地 EEG'}).click();require(await desktop.getByText('26 折模型就绪').count()===1,'26 折权重状态');
 await desktop.getByRole('button',{name:/建立新校准档案/}).click();require(await desktop.getByText('目标被试对应的模型折').count()===1,'模型折选择');
 await desktop.screenshot({path:fileURLToPath(new URL('lab-real-desktop.png',output)),fullPage:true});
 const mobile=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1});mobile.on('pageerror',e=>failures.push('mobile JS: '+e.message));
 await visit(mobile,'/');await mobile.screenshot({path:fileURLToPath(new URL('home-mobile.png',output)),fullPage:true});
 require(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'首页手机宽度无横向溢出');
 await visit(mobile,'/lab');await mobile.getByRole('button',{name:'运行演示'}).click();await mobile.getByText('以下为预设演示结果，不是网络实际预测。').waitFor();await mobile.screenshot({path:fileURLToPath(new URL('lab-mobile.png',output)),fullPage:true});
 require(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'工作台手机宽度无横向溢出');
 const flat=await browser.newPage({viewport:{width:1000,height:700}});await flat.addInitScript(()=>{const old=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){if(type==='webgl2'||type==='webgl')return null;return old.call(this,type,...args)}});
 await visit(flat,'/');require(await flat.locator('.flat-network').count()>=1,'WebGL 降级');
 const reduced=await browser.newPage({viewport:{width:1000,height:700},reducedMotion:'reduce'});await visit(reduced,'/');require(await reduced.getByRole('button',{name:/播放数据流/}).isDisabled(),'减少动态效果');
 const template=await desktop.request.get(origin+'/api/templates/example_eeg.npz');require(template.ok()&&Number(template.headers()['content-length'])>10000,'模板下载');
 console.log(JSON.stringify({passed:failures.length===0,failures,screenshots:['home-desktop','lab-desktop','lab-real-desktop','home-mobile','lab-mobile'],modelStatus:'26 folds'}));if(failures.length)process.exitCode=1;
}finally{await browser.close()}
