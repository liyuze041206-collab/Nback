export async function api<T=any>(path:string,options?:RequestInit):Promise<T>{
 const response=await fetch(`/api${path}`,options);
 if(!response.ok){let message='请求失败，请重试。';try{const body=await response.json();message=typeof body.detail==='string'?body.detail:body.detail?.map((x:any)=>x.msg).join('；')||message}catch{message='本地服务不可用，请检查是否已启动。'}throw new Error(message)}
 return response.json();
}
export const post=(path:string,data:unknown)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
export type Recording={id:string,name:string,duration_sec:number,sfreq:number,samples:number,channels:string[],events:{onset_sec:number,type:string}[],event_types:string[],preview?:any[],unit:string,original_unit?:string,extra_channels_ignored?:string[]};
export type Profile={id:string,subject:string,session:string,shots:number,window_mode:'continuous'|'events',event_types:string[],model_version:string,recording_ids:string[],model_id?:string};
export type Status={model:{ready:boolean,message:string,version:string|null,label:string,temporal_ready:boolean,device:string,mode?:'single'|'folds',folds?:{id:string,ready:boolean,version:string|null,temporal_ready:boolean,message:string,validation_subject:string}[]},channels:string[]};
