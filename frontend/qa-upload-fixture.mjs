// UI-only fixture. Real model/upload checks live in qa-prepared-upload.mjs.
export async function mockUploadAnalysis(page,{id='upload-ui-test',windows=20,delay=0}={}){
  let uploads=0;
  await page.route('**/api/recordings',async route=>{
    if(route.request().method()!=='POST')return route.continue();
    await route.fulfill({status:201,json:{id:`ui-record-${++uploads}`}});
  });
  await page.route('**/api/recordings/*/prepare-analysis',route=>route.fulfill({json:{subject:'sub-01',session:'ses-S1',model_id:'sub-01',validation_subject:'sub-02',calibration_id:'ui-calibration',window_mode:'continuous',event_types:[]}}));
  await page.route('**/api/analyses',async route=>{
    if(route.request().method()!=='POST')return route.continue();
    await route.fulfill({status:202,json:{id}});
  });
  await page.route(`**/api/analyses/${id}`,async route=>{
    if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
    await route.fulfill({json:{id,status:'complete',mode:'real',temporal_enabled:false,sfreq:250,final_class_label:0,vote_0:windows,vote_2:0,
      rows:Array.from({length:windows},(_,i)=>({window:i+1,start_sec:i*2,end_sec:i*2+2,prob_0:.8,prob_2:.2,class_label:0}))}});
  });
}

export async function selectMockUpload(scope,name='sub-01_ses-S1_query.npz'){
  await scope.locator('#queryFile').setInputFiles({name,mimeType:'application/octet-stream',buffer:Buffer.from('UI-only mock NPZ')});
  await scope.locator('#matchStatus').filter({hasText:'已自动匹配'}).waitFor();
}
