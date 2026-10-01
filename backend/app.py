from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gspm.engine import ModelCatalog, calibrate, predict, digest
from gspm.demo import demo_catalog, synthetic_record, window_wave
from gspm.signal import CHANNELS, FEATURE_CONFIG, MAX_BYTES, InputError, extract, parse_recording, preview

ROOT=Path(__file__).resolve().parent.parent


def summarize_rows(rows):
    """Return one recording-level 0/2 decision from final per-window decisions."""
    labels=np.asarray([row['class_label'] for row in rows],dtype=int)
    votes_0=int(np.sum(labels==0));votes_2=int(np.sum(labels==2))
    if votes_0==votes_2:
        probability_key='state_prob_2' if 'state_prob_2' in rows[0] else 'prob_2'
        final_label=2 if np.mean([row[probability_key] for row in rows])>=.5 else 0
        rule='majority_vote_then_mean_probability'
    else:
        final_label=0 if votes_0>votes_2 else 2
        rule='majority_vote'
    return {'final_class_label':final_label,'vote_0':votes_0,'vote_2':votes_2,
            'final_vote_ratio':max(votes_0,votes_2)/len(rows),'final_rule':rule}


class CalibrationRequest(BaseModel):
    record_0: str
    record_2: str
    subject: str = Field(min_length=1,max_length=80)
    session: str = Field(min_length=1,max_length=80)
    shots: int = Field(default=20,ge=2,le=100)
    model_id: str | None = None
    window_mode: Literal['continuous','events']='continuous'
    event_types: list[str] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    recording_id: str
    calibration_id: str
    subject: str = Field(min_length=1,max_length=80)
    session: str = Field(min_length=1,max_length=80)
    window_mode: Literal['continuous','events']='continuous'
    event_types: list[str] = Field(default_factory=list)


def create_app(data_root=None,model_root=None,support_root=None):
    root=Path(data_root or os.environ.get('GSPM_DATA_DIR',ROOT/'runtime')).resolve()
    for sub in ['recordings','calibrations','analyses']:(root/sub).mkdir(parents=True,exist_ok=True)
    engine=ModelCatalog(model_root or ROOT/'backend/models')
    support_directory=Path(support_root or os.environ.get('GSPM_SUPPORT_DIR',ROOT.parent/'真实数据测试'/'网页上传数据')).resolve()
    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='gspm')
    lock=threading.RLock()
    demo_jobs={}
    torch.set_num_threads(4)

    def path(kind,key,ext='.json'):
        try:
            if str(uuid.UUID(key))!=key:raise ValueError()
        except ValueError:raise HTTPException(404,'找不到该记录。')
        result=root/kind/(key+ext)
        if result.is_symlink():raise HTTPException(400,'记录路径无效。')
        return result

    def read(kind,key):
        with lock:
            p=path(kind,key)
            if not p.is_file():raise HTTPException(404,'记录不存在或已删除。')
            return json.loads(p.read_text(encoding='utf-8'))

    def save(kind,key,value):
        with lock:
            p=path(kind,key);temp=p.with_suffix('.tmp')
            temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8');temp.replace(p)

    def items(kind):
        with lock:
            return sorted([json.loads(p.read_text(encoding='utf-8')) for p in (root/kind).glob('*.json')],key=lambda v:v.get('created_at',0),reverse=True)

    def load_signal(key):
        meta=read('recordings',key)
        return np.load(path('recordings',key,'.npy'),allow_pickle=False),meta

    def require_model(model_id=None,subject=None):
        try:return engine.require(model_id,subject)
        except InputError as exc:raise HTTPException(409,str(exc)) from exc

    def store_recording(data,meta,reuse=False):
        with lock:
            if reuse:
                for existing in items('recordings'):
                    if (existing['fingerprint']==meta['fingerprint'] and
                        existing.get('prepared')==meta.get('prepared') and
                        path('recordings',existing['id'],'.npy').is_file()):
                        return existing
            key=str(uuid.uuid4());meta={**meta,'id':key,'created_at':time.time()}
            np.save(path('recordings',key,'.npy'),data,allow_pickle=False)
            save('recordings',key,meta)
            return meta

    def check_prepared_identity(meta):
        info=meta.get('prepared')
        if not info:raise InputError('请上传带被试、session 和用途信息的网页专用 NPZ；普通 EEG 请使用完整工作台。')
        if not re.fullmatch(r'sub-\d{2}',info['subject']) or not re.fullmatch(r'ses-[A-Za-z0-9_-]+',info['session']):
            raise InputError('文件的被试或 session 标识无效，请补充正确的文件信息。')
        for pattern,field in [(r'(?i)(?<![a-z0-9])sub-\d+(?!\d)','subject'),
                              (r'(?i)(?<![a-z0-9])ses-[a-z0-9]+','session')]:
            if any(value.lower()!=info[field].lower() for value in re.findall(pattern,meta['name'])):
                raise InputError('文件名的被试或 session 与文件内部信息不一致，请检查文件。')
        return info

    def check_support_query(record,support_records):
        info=record['prepared']
        supports=[m.get('prepared') for m in support_records]
        if len(supports)!=2:raise InputError('校准需要两类独立支持信号。')
        for meta,s,role in zip(support_records,supports,['support0','support2']):
            if not s or (s['role'],s['subject'],s['session'])!=(role,info['subject'],info['session']):
                raise InputError('支持文件的用途、被试或 session 与查询不匹配。')
            if meta['fingerprint']==record['fingerprint']:
                raise InputError('查询信号已用于支持校准，请使用独立查询文件。')
            if s['source_id']==info['source_id'] and any(abs(a-b)<2.-1e-6 for a in s['onsets'] for b in info['onsets']):
                raise InputError('查询窗口与支持窗口有时间重叠，请使用独立划分的数据。')
        if support_records[0]['fingerprint']==support_records[1]['fingerprint']:
            raise InputError('两类支持集不能使用同一段信号。')
        if info['source_id'] not in {s['source_id'] for s in supports}:
            raise InputError('查询与支持数据的来源不匹配，请使用同一数据划分的文件。')
        if len(supports[0]['onsets'])!=len(supports[1]['onsets']):
            raise InputError('两类支持窗口数量不一致。')

    @asynccontextmanager
    async def lifespan(app):
        for job in items('analyses'):
            if job['status'] in ['queued','running']:
                job.update(status='failed',error='上次服务停止时任务尚未完成，请重新分析。')
                save('analyses',job['id'],job)
        yield
        executor.shutdown(wait=True)

    app=FastAPI(title='GSPM-Net Local API',version='1.0.0',lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    app.add_middleware(CORSMiddleware,allow_origins=['null','http://127.0.0.1:8000','http://localhost:8000'],allow_methods=['*'],allow_headers=['*'])
    app.state.engine=engine

    @app.middleware('http')
    async def local_origin(request,call_next):
        origin=request.headers.get('origin')
        allowed={'null','http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000','http://testserver'}
        if request.url.hostname in {'127.0.0.1','localhost','testserver'}:
            allowed.add(str(request.base_url).rstrip('/'))
        if origin and origin not in allowed:
            return JSONResponse({'detail':'仅接受本机工作台请求。'},status_code=403)
        return await call_next(request)

    @app.exception_handler(InputError)
    async def invalid_input(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=422)

    @app.get('/api/status')
    def status():
        return {'service':'GSPM-Net','version':'1.0.0','model':engine.status(),'channels':CHANNELS,
                'preprocessing':FEATURE_CONFIG,'data_policy':'local_only','demo_is_real_inference':False}

    @app.get('/api/demo/examples')
    def demo_examples():
        return {'examples':demo_catalog(),'model':engine.status()}

    def run_demo(key, example_id, model_id):
        job=demo_jobs[key]
        try:
            model,version,config=require_model(model_id,model_id)
            data0,m0=synthetic_record(example_id,'support0',1101)
            data2,m2=synthetic_record(example_id,'support2',2202)
            query,meta=synthetic_record(example_id,'query',3303)
            X0,_,a0=extract(data0,m0,'continuous');X2,_,a2=extract(data2,m2,'continuous');Xq,times,audit=extract(query,meta,'continuous')
            baseline=calibrate(model,X0,X2,20)
            job.update(status='running',progress=45)
            rows=predict(model,Xq,baseline,times,config.get('temporal'))
            job.update(status='complete',progress=100,rows=rows,windows=len(rows),duration_sec=meta['duration_sec'],
                       temporal_enabled=bool(config.get('temporal')),model_version=version,
                       audit={'support_0':a0,'support_2':a2,'query':audit},example_id=example_id,
                       example_name=next(v['name'] for v in demo_catalog() if v['id']==example_id),
                       wave=window_wave(query, sfreq=meta['sfreq']),sfreq=meta['sfreq'],
                       channels=CHANNELS[:7],notice='合成 EEG 示例，使用真实模型权重计算，不代表实测识别效果。',
                       **summarize_rows(rows))
        except Exception as exc:
            job.update(status='failed',progress=100,error=str(exc)[:600])

    @app.post('/api/demo/analyses',status_code=202)
    def create_demo(payload:dict):
        example_id=payload.get('example_id')
        if example_id not in {v['id'] for v in demo_catalog()}:raise HTTPException(422,'请选择有效的固定演示示例。')
        model_id=payload.get('model_id') or 'sub-01'
        require_model(model_id,model_id)
        key=str(uuid.uuid4());demo_jobs[key]={'id':key,'mode':'demo-real','status':'queued','progress':5,'example_id':example_id,'model_id':model_id}
        executor.submit(run_demo,key,example_id,model_id)
        return demo_jobs[key]

    @app.get('/api/demo/analyses/{key}')
    def demo_analysis(key:str):
        if key not in demo_jobs:raise HTTPException(404,'演示任务不存在或服务已重启。')
        return demo_jobs[key]

    @app.get('/api/demo/analyses/{key}/windows/{index}/preview')
    def demo_window(key:str,index:int):
        job=demo_jobs.get(key)
        if not job or job.get('status')!='complete':raise HTTPException(409,'演示任务尚未完成。')
        if index<0 or index>=len(job['wave']):raise HTTPException(404,'窗口不存在。')
        return {'window':index+1,'start_sec':index*2,'end_sec':index*2+2,'sfreq':job['sfreq'],'channels':job['channels'],'samples':job['wave'][index]}

    @app.post('/api/recordings',status_code=201)
    async def upload(files:list[UploadFile]=File(...),sfreq:float|None=Form(None),unit:str|None=Form(None),event_onsets:str|None=Form(None)):
        payload={};size=0
        for f in files:
            name=(f.filename or '').replace('\\','/').split('/')[-1]
            if Path(name).suffix.lower() not in {'.set','.fdt','.csv','.npz'}:raise InputError('仅支持 CSV、NPZ、SET / FDT。')
            if name in payload:raise InputError('文件名重复。')
            chunks=[]
            while chunk:=await f.read(1024*1024):
                size+=len(chunk)
                if size>MAX_BYTES:raise InputError('单次导入总大小不能超过 256 MB。')
                chunks.append(chunk)
            payload[name]=b''.join(chunks)
        manual=None
        if event_onsets and event_onsets.strip():
            try:
                manual=json.loads(event_onsets)
                if not isinstance(manual,list) or any(isinstance(t,bool) or not isinstance(t,(int,float)) for t in manual):raise ValueError()
            except ValueError:raise InputError('事件时间请输入秒数数组，例如 [0, 2, 4]。')
        data,meta=parse_recording(payload,sfreq,unit,manual)
        meta=store_recording(data,meta)
        return {**meta,'preview':preview(data,meta['sfreq'])}

    @app.get('/api/recordings')
    def recordings():return items('recordings')

    @app.get('/api/recordings/{key}')
    def recording(key:str):
        data,meta=load_signal(key)
        return {**meta,'preview':preview(data,meta['sfreq'])}

    @app.delete('/api/recordings/{key}')
    def delete_recording(key:str):
        with lock:
            read('recordings',key)
            if any(key in v['recording_ids'] for v in items('calibrations')):
                raise HTTPException(409,'该记录被校准档案引用，请先删除对应校准档案。')
            if any(v.get('recording_id')==key and v['status'] in ['queued','running'] for v in items('analyses')):
                raise HTTPException(409,'记录正在分析，请等待任务完成。')
            path('recordings',key,'.npy').unlink(missing_ok=True);path('recordings',key).unlink()
        return {'deleted':key}

    @app.get('/api/calibrations')
    def calibrations():return items('calibrations')

    @app.post('/api/calibrations',status_code=201)
    def create_calibration(req:CalibrationRequest):
        model,version,config=require_model(req.model_id,req.subject)
        with lock:
            d0,m0=load_signal(req.record_0);d2,m2=load_signal(req.record_2)
            if m0['fingerprint']==m2['fingerprint']:raise InputError('两类支持集不能使用同一段信号。')
            prepared_support = []
            for meta, role, allowed in [(m0,'support0',{'6021','6022'}),(m2,'support2',{'6221','6222'})]:
                info = meta.get('prepared')
                if info:
                    if (info['role'],info['subject'],info['session']) != (role,req.subject,req.session):
                        raise InputError('支持文件的用途、被试或 session 不匹配，请选择同一文件夹内对应的支持文件。')
                    if len(info['onsets']) != req.shots:
                        raise InputError('校准窗口数与预处理文件中的支持集划分不一致。')
                    prepared_support.append(info)
                else:
                    codes=set(meta.get('event_types', []))
                    if codes & {'6021','6022','6121','6122','6221','6222'} and not codes & allowed:
                        raise InputError('支持记录的刺激类型与所选 0-back / 2-back 类别不符；不能使用 1-back。')
            if len(prepared_support) == 1:
                raise InputError('两类支持文件必须采用相同的预处理格式。')
            X0,_,a0=extract(d0,m0,req.window_mode,req.event_types)
            X2,_,a2=extract(d2,m2,req.window_mode,req.event_types)
            baseline=calibrate(model,X0,X2,req.shots)
            key=str(uuid.uuid4())
            meta=dict(id=key,created_at=time.time(),subject=req.subject,session=req.session,shots=req.shots,
                model_version=version,model_name=config.get('name','GSC'),model_id=config.get('target_subject',req.model_id),
                recording_ids=[req.record_0,req.record_2],
                fingerprints=[m0['fingerprint'],m2['fingerprint']],preprocessing=FEATURE_CONFIG,
                window_mode=req.window_mode,event_types=req.event_types,protocol='single_session_application',
                query_statistics_used=False,audit=[a0,a2],prepared_support=prepared_support)
            if prepared_support:meta['protocol']='paper_20_per_class_7_7_6_session_subset'
            np.savez(path('calibrations',key,'.npz'),**baseline)
            save('calibrations',key,meta)
        return meta

    @app.post('/api/recordings/{key}/prepare-analysis')
    def prepare_analysis(key:str):
        with lock:
            _,record=load_signal(key)
            info=check_prepared_identity(record)
            if info['role']!='query':raise InputError('请选择待分类的查询 NPZ，不要上传支持文件。')
            subject,session=info['subject'],info['session']
            if subject not in engine.folds:raise HTTPException(409,f'未安装 {subject} 对应的模型折，无法自动匹配。')
            _,version,config=require_model(subject,subject)

            def response(profile):
                return dict(subject=subject,session=session,model_id=subject,
                            validation_subject=config.get('validation_subject'),calibration_id=profile['id'],
                            window_mode=profile['window_mode'],event_types=profile['event_types'])

            # Ignore obsolete or damaged profiles, then try local support data.
            candidates=[]
            for file in (root/'calibrations').glob('*.json'):
                try:
                    profile=read('calibrations',file.stem)
                    if ((profile.get('subject'),profile.get('session'),profile.get('model_id'),profile.get('model_version'))
                        != (subject,session,subject,version) or digest(profile.get('preprocessing'))!=digest(FEATURE_CONFIG)):
                        continue
                    if profile.get('window_mode') not in {'continuous','events'} or not isinstance(profile.get('event_types'),list):continue
                    records=[load_signal(k)[1] for k in profile['recording_ids']]
                    for meta in records:check_prepared_identity(meta)
                    check_support_query(record,records)
                    if profile.get('prepared_support')!=[m['prepared'] for m in records]:continue
                    if profile.get('fingerprints')!=[m['fingerprint'] for m in records]:continue
                    if profile.get('shots')!=len(records[0]['prepared']['onsets']):continue
                    with np.load(path('calibrations',profile['id'],'.npz'),allow_pickle=False) as baseline:
                        shapes={'mean':(5,62),'scale':(5,62),'means':(2,96),'variances':(2,96)}
                        if set(baseline.files)!=set(shapes):continue
                        if any(baseline[k].shape!=shape or not np.isfinite(baseline[k]).all() for k,shape in shapes.items()):continue
                        if np.any(baseline['scale']<=0) or np.any(baseline['variances']<=0):continue
                    candidates.append(profile)
                except (HTTPException,InputError,OSError,ValueError,KeyError,TypeError,EOFError,zipfile.BadZipFile):
                    continue
            if candidates:
                return response(max(candidates,key=lambda p:(p['created_at'],p['id'])))

            folder=(support_directory/subject/session).resolve()
            if not folder.is_relative_to(support_directory) or not folder.is_dir():
                raise HTTPException(409,f'未找到 {subject} · {session} 的校准档案或本机支持文件，请配置 GSPM_SUPPORT_DIR。')
            supports={'support0':[],'support2':[]}
            for file in sorted(folder.glob('*.npz')):
                # Read just the manifest first; query NPZs can be much larger.
                if not file.resolve().is_relative_to(support_directory):raise HTTPException(409,'支持文件路径超出本机支持目录。')
                if file.stat().st_size>MAX_BYTES:raise HTTPException(409,'本机支持目录包含过大的 NPZ。')
                try:
                    with np.load(file,allow_pickle=False) as z:
                        with zipfile.ZipFile(file) as archive:
                            if sum(i.file_size for i in archive.infolist())>MAX_BYTES:raise ValueError('NPZ 解压后过大')
                        manifest=json.loads(str(z['prepared_manifest'].item()))
                    if manifest.get('role') not in supports:continue
                    data,meta=parse_recording({file.name:file.read_bytes()})
                    s=check_prepared_identity(meta)
                    if (s['subject'],s['session'])!=(subject,session):raise InputError('支持文件的被试或 session 与所在目录不一致。')
                    supports[s['role']].append((data,meta))
                except (OSError,ValueError,KeyError,TypeError,EOFError,zipfile.BadZipFile) as exc:
                    raise HTTPException(409,f'本机支持文件 {file.name} 无法使用：{str(exc)[:180]}') from exc
            if any(len(v)!=1 for v in supports.values()):
                raise HTTPException(409,'本机支持文件缺失或有重复候选；每类应恰好有一个支持 NPZ。')
            pair=[supports[role][0] for role in ['support0','support2']]
            try:check_support_query(record,[meta for _,meta in pair])
            except InputError as exc:raise HTTPException(409,str(exc)) from exc
            shots=len(pair[0][1]['prepared']['onsets'])
            if not 2<=shots<=100:raise HTTPException(409,'本机支持窗口数应在每类 2–100 个范围内。')
            records=[store_recording(data,meta,reuse=True) for data,meta in pair]
            profile=create_calibration(CalibrationRequest(record_0=records[0]['id'],record_2=records[1]['id'],
                subject=subject,session=session,shots=shots,model_id=subject,window_mode='continuous',event_types=[]))
            return response(profile)

    @app.delete('/api/calibrations/{key}')
    def delete_calibration(key:str):
        with lock:
            read('calibrations',key)
            if any(v.get('calibration_id')==key and v['status'] in ['queued','running'] for v in items('analyses')):raise HTTPException(409,'校准档案正在被任务使用。')
            path('calibrations',key,'.npz').unlink(missing_ok=True);path('calibrations',key).unlink()
        return {'deleted':key}

    def run_analysis(key,model,baseline,config,req):
        job=read('analyses',key)
        def update(progress):
            job.update(status='running',progress=progress)
            with lock:save('analyses',key,job)
        try:
            update(10);data,meta=load_signal(req.recording_id)
            features,times,audit=extract(data,meta,req.window_mode,req.event_types)
            update(40)
            rows=predict(model,features,baseline,times,config.get('temporal'),update)
            labels=np.array([r['class_label'] for r in rows])
            job.update(status='complete',progress=100,rows=rows,audit=audit,temporal_enabled=bool(config.get('temporal')),
                       high_ratio=float((labels==2).mean()),switches=int(np.sum(labels[1:]!=labels[:-1])),
                       duration_sec=meta['duration_sec'],windows=len(rows),mode='real',
                       psd=features.mean(axis=0).tolist(),wave=(window_wave(data, sfreq=meta['sfreq'])
                           if meta.get('prepared') or req.window_mode=='continuous' else
                           [data[:7,round(t*meta['sfreq']):round(t*meta['sfreq'])+round(2*meta['sfreq'])].tolist() for t in times]),
                       display_channels=meta['channels'][:7],sfreq=meta['sfreq'],completed_at=time.time(),
                       **summarize_rows(rows))
        except Exception as exc:
            job.update(status='failed',error=str(exc)[:600],progress=100)
        with lock:save('analyses',key,job)

    @app.post('/api/analyses',status_code=202)
    def create_analysis(req:AnalysisRequest):
        # Fail with the actionable model state before attempting to resolve
        # user-supplied recording or calibration identifiers.
        if not engine.status().get('ready'):
            require_model(None,req.subject)
        with lock:
            profile=read('calibrations',req.calibration_id);record=read('recordings',req.recording_id)
            codes=set(record.get('event_types', []))
            if codes & {'6121','6122'}:
                raise InputError('该查询包含 1-back 刺激；当前模型仅用于 0-back / 2-back。')
            model,version,config=require_model(profile.get('model_id'),req.subject)
            if profile['model_version']!=version:raise HTTPException(409,'模型已更换，请重新校准。')
            if digest(profile.get('preprocessing'))!=digest(FEATURE_CONFIG):raise HTTPException(409,'预处理或通道配置已变化，请重新校准。')
            if (profile['subject'],profile['session'])!=(req.subject,req.session):raise InputError('查询记录必须与校准档案属于同一用户和 session。')
            if record['fingerprint'] in profile['fingerprints']:raise InputError('该信号已用于校准，不能再次作为查询记录评分。请上传独立查询片段。')
            info=record.get('prepared');support=profile.get('prepared_support',[])
            if bool(info)!=bool(support):raise InputError('查询与支持文件必须采用相同的预处理格式。')
            if info:
                if (info['role'],info['subject'],info['session']) != ('query',req.subject,req.session):
                    raise InputError('查询文件的用途、被试或 session 与校准档案不匹配。')
                for item in support:
                    if item['source_id']==info['source_id'] and any(abs(a-b)<2.-1e-6 for a in item['onsets'] for b in info['onsets']):
                        raise InputError('查询窗口与支持窗口有时间重叠，请使用独立划分的数据。')
            if profile['window_mode']!=req.window_mode or profile['event_types']!=req.event_types:raise InputError('查询分窗方式和刺激事件类型需与校准档案一致。')
            if len([v for v in items('analyses') if v['status'] in ['queued','running']])>=8:raise HTTPException(429,'任务队列已满，请等待当前分析完成。')
            with np.load(path('calibrations',req.calibration_id,'.npz'),allow_pickle=False) as z:baseline={k:z[k].copy() for k in z.files}
            key=str(uuid.uuid4());job=dict(id=key,created_at=time.time(),status='queued',progress=0,mode='real',
                recording_id=req.recording_id,calibration_id=req.calibration_id,subject=req.subject,session=req.session,
                model_version=version,recording_name=record['name'])
            save('analyses',key,job)
            executor.submit(run_analysis,key,model,baseline,config,req)
        return job

    @app.get('/api/analyses')
    def analyses():return [{k:v for k,v in a.items() if k not in {'rows','psd'}} for a in items('analyses')]

    @app.get('/api/analyses/{key}')
    def analysis(key:str):return read('analyses',key)

    @app.delete('/api/analyses/{key}')
    def delete_analysis(key:str):
        with lock:
            job=read('analyses',key)
            if job['status'] in ['queued','running']:raise HTTPException(409,'请等待分析完成后再删除。')
            path('analyses',key).unlink()
        return {'deleted':key}

    @app.get('/api/analyses/{key}/export')
    def export(key:str,format:Literal['csv','json']='csv'):
        job=read('analyses',key)
        if job['status']!='complete':raise HTTPException(409,'任务尚未成功完成。')
        if format=='json':return Response(json.dumps(job,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="gspm-{key}.json"'})
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=list(job['rows'][0]))
        writer.writeheader();writer.writerows(job['rows'])
        return Response('\ufeff'+stream.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="gspm-{key}.csv"'})

    @app.get('/api/templates/{name}')
    def templates(name:str):
        if name not in {'example_eeg.csv','example_eeg.npz','README.md'}:raise HTTPException(404)
        p=ROOT/'examples'/name
        if not p.is_file():raise HTTPException(404,'模板尚未生成，请执行安装脚本。')
        return FileResponse(p,filename=name)

    @app.get('/architecture',include_in_schema=False)
    def architecture_page():
        page=ROOT/'gspm_network_atlas.html'
        if not page.is_file():raise HTTPException(404,'架构介绍页面不存在。')
        return FileResponse(page,media_type='text/html')

    @app.get('/demo',include_in_schema=False)
    def demo_page():
        page=ROOT/'gspm_eeg_workload_demo.html'
        if not page.is_file():
            page=ROOT.parent/'gspm_eeg_workload_demo.html'
        if not page.is_file():raise HTTPException(404,'Demo 页面不存在。')
        return FileResponse(page,media_type='text/html')

    @app.get('/{rest:path}',include_in_schema=False)
    def frontend(rest:str):
        if rest.startswith('api/'):raise HTTPException(404,'接口不存在。')
        dist=(ROOT/'frontend/dist').resolve();target=(dist/rest).resolve()
        if not target.is_relative_to(dist):raise HTTPException(404)
        if target.is_file():return FileResponse(target)
        if (dist/'index.html').is_file():return FileResponse(dist/'index.html')
        raise HTTPException(503,'前端尚未构建，请执行安装脚本。')
    return app


app=create_app()
