"""Real NPZ auto/manual parity check in an isolated temporary data directory."""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'backend'))
from app import create_app
from gspm.engine import calibrate,predict
from gspm.signal import extract,parse_recording


def main():
    support=Path(os.environ.get('GSPM_SUPPORT_DIR',ROOT.parent/'真实数据测试'/'网页上传数据'))
    cases=[('sub-01','ses-S1',7),('sub-05','ses-S2',7),('sub-18','ses-S3',6)]
    report=[]
    with tempfile.TemporaryDirectory(prefix='gspm-auto-') as temporary:
        app=create_app(temporary,ROOT/'backend/models',support)
        with TestClient(app) as client:
            for subject,session,shots in cases:
                folder=support/subject/session
                query=folder/f'{subject}_{session}_查询_2-back.npz'
                content=query.read_bytes()
                response=client.post('/api/recordings',files=[('files',(query.name,content))])
                assert response.status_code==201,response.text
                key=response.json()['id']
                prepared=client.post(f'/api/recordings/{key}/prepare-analysis')
                assert prepared.status_code==200,prepared.text
                ready=prepared.json();assert ready['model_id']==subject
                profiles=client.get('/api/calibrations').json()
                assert next(p for p in profiles if p['id']==ready['calibration_id'])['shots']==shots
                before=len(client.get('/api/recordings').json())
                assert client.post(f'/api/recordings/{key}/prepare-analysis').json()==ready
                assert len(client.get('/api/recordings').json())==before
                payload={k:ready[k] for k in ['subject','session','calibration_id','window_mode','event_types']}
                response=client.post('/api/analyses',json={**payload,'recording_id':key})
                assert response.status_code==202,response.text
                job_id=response.json()['id'];deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    job=client.get('/api/analyses/'+job_id).json()
                    if job['status'] in ['complete','failed']:break
                    time.sleep(.05)
                assert job['status']=='complete',job.get('error')
                model,_,config=app.state.engine.require(subject,subject)
                supports=[]
                for label in [0,2]:
                    p=folder/f'{subject}_{session}_支持_{label}-back.npz'
                    data,meta=parse_recording({p.name:p.read_bytes()})
                    supports.append(extract(data,meta)[0])
                data,meta=parse_recording({query.name:content})
                features,times,_=extract(data,meta)
                manual=predict(model,features,calibrate(model,*supports,shots),times,config.get('temporal'))
                assert job['rows']==manual,'Auto/manual predictions differ'
                report.append(dict(subject=subject,session=session,shots=shots,windows=job['windows'],
                    model_id=ready['model_id'],validation_subject=ready['validation_subject'],
                    final_class_label=job['final_class_label'],auto_manual_identical=True,idempotent=True))
                print(f"Verified {subject}/{session}: {job['windows']} windows, {shots} support/class",flush=True)
    destination=ROOT/'docs'/'auto-upload-validation.json'
    destination.write_text(json.dumps(dict(passed=True,cases=report),ensure_ascii=False,indent=2),encoding='utf-8')
    print('Report: '+str(destination))


if __name__=='__main__':main()
