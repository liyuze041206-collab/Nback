"""Real-checkpoint integration check of prepared uploads, with isolated runtime."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from fastapi.testclient import TestClient
from app import create_app
from gspm.signal import parse_recording, InputError

base=ROOT.parent/'真实数据测试'
folder=base/'网页上传数据/sub-01/ses-S1'
reference=list(csv.DictReader((base/'results/window_predictions.csv').open(encoding='utf-8-sig')))
audit=[]
with tempfile.TemporaryDirectory(prefix='web-validation-',dir=ROOT/'runtime') as runtime:
    with TestClient(create_app(runtime)) as client:
        assert client.get('/demo').status_code==200
        def upload(role,task):
            path=folder/f'sub-01_ses-S1_{role}_{task}-back.npz'
            r=client.post('/api/recordings',files=[('files',(path.name,path.read_bytes()))])
            assert r.status_code==201,r.text
            return r.json()
        a,b=upload('支持',0),upload('支持',2)
        request=dict(record_0=a['id'],record_2=b['id'],subject='sub-01',session='ses-S1',shots=7,model_id='sub-01')
        assert client.post('/api/calibrations',json={**request,'shots':20}).status_code==422
        assert client.post('/api/calibrations',json={**request,'record_0':b['id'],'record_2':a['id']}).status_code==422
        assert client.post('/api/calibrations',json={**request,'model_id':'sub-05'}).status_code==409
        r=client.post('/api/calibrations',json=request);assert r.status_code==201,r.text
        profile=r.json()
        saved=(Path(runtime)/'calibrations'/f"{profile['id']}.npz").read_bytes()
        for task in [0,2]:
            q=upload('查询',task)
            payload=dict(recording_id=q['id'],calibration_id=profile['id'],subject='sub-01',session='ses-S1')
            assert client.post('/api/analyses',json={**payload,'recording_id':a['id']}).status_code==422
            r=client.post('/api/analyses',json=payload);assert r.status_code==202,r.text
            key=r.json()['id'];deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                job=client.get('/api/analyses/'+key).json()
                if job['status'] in {'complete','failed'}:break
                time.sleep(.05)
            assert job['status']=='complete',job
            assert job['final_class_label']==task
            assert job['vote_0']+job['vote_2']==job['windows']
            expected=[x for x in reference if x['subject']=='sub-01' and x['session']=='ses-S1' and x['task']==f'{task}-back']
            assert len(job['rows'])==len(expected)==len(job['wave'])==137
            for row,old in zip(job['rows'],expected):
                assert row['class_label']==int(old['predicted_label'])
                np.testing.assert_allclose([row[k] for k in ['prob_0','prob_2','state_prob_0','state_prob_2']],
                    [float(old[k]) for k in ['prob_0','prob_2','state_prob_0','state_prob_2']],rtol=1e-4,atol=2e-5)
                assert abs(row['start_sec']-float(old['epoch_start_sec']))<.0001
            assert (Path(runtime)/'calibrations'/f"{profile['id']}.npz").read_bytes()==saved
            audit.append(dict(task=f'{task}-back',windows=len(expected),predictions_match_offline=True))
        # A changed data value cannot bypass the source/time overlap guard.
        support_file=folder/'sub-01_ses-S1_支持_0-back.npz'
        with np.load(support_file,allow_pickle=False) as z:fields={k:z[k].copy() for k in z.files}
        meta=json.loads(fields['prepared_manifest'].item());meta['role']='query'
        fields['prepared_manifest']=json.dumps(meta);fields['eeg'][0,0]+=.1
        stream=io.BytesIO();np.savez(stream,**fields)
        r=client.post('/api/recordings',files=[('files',('overlap.npz',stream.getvalue()))]);assert r.status_code==201
        payload['recording_id']=r.json()['id']
        r=client.post('/api/analyses',json=payload);assert r.status_code==422 and '重叠' in r.text
        meta['feature_config']['log_epsilon']=1e-12
        fields['prepared_manifest']=json.dumps(meta);stream=io.BytesIO();np.savez(stream,**fields)
        try:parse_recording({'bad.npz':stream.getvalue()})
        except InputError:pass
        else:raise AssertionError('Mismatched preprocessing configuration accepted')
(base/'网页上传数据/网页推理校验.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(audit,ensure_ascii=False))
