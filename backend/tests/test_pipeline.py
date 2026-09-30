import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient
from scipy.io import savemat

from app import create_app
from gspm.signal import CHANNELS, FEATURE_CONFIG, InputError, parse_recording, extract
from gspm.engine import Engine, calibrate, predict, temporal_decision
from gspm.network import EnhancedGatedSpectralChannelNet


def npz_bytes(seed=1, seconds=8, channels=None, sfreq=250, **extra):
    channels = channels or CHANNELS
    data = np.random.default_rng(seed).normal(size=(int(seconds*sfreq),len(channels)))
    fields=dict(eeg=data,sfreq=sfreq,ch_names=np.array(channels),unit='uV')
    fields.update(extra)
    b=io.BytesIO();np.savez_compressed(b,**fields);return b.getvalue()


@pytest.fixture
def model_dir(tmp_path):
    target=tmp_path/'models';target.mkdir()
    manifest=json.loads((Path(__file__).parents[1]/'models/manifest.example.json').read_text(encoding='utf-8'))
    manifest['name']='TEST ONLY / random weights'
    (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    torch.manual_seed(2026)
    torch.save(EnhancedGatedSpectralChannelNet(5,62,96,128,.25).state_dict(),target/'encoder.pt')
    return target


def test_channel_alignment_units_and_csv():
    data=np.arange(500*62,dtype=float).reshape(500,62)/100000
    raw,meta=parse_recording({'x.npz':npz_bytes(seconds=2,channels=CHANNELS[::-1],eeg=data[:,::-1],unit='mV')})
    np.testing.assert_array_equal(raw,data.T*1000)
    assert meta['channels']==CHANNELS and meta['unit']=='uV'
    output=io.StringIO();np.savetxt(output,np.c_[np.arange(500)/250,data],delimiter=',',header=','.join(['time_s']+CHANNELS),comments='')
    csv=output.getvalue().encode()
    a,m=parse_recording({'x.csv':csv},250,'uV');np.testing.assert_allclose(a,data.T)
    with pytest.raises(InputError,match='采样率'):parse_recording({'x.csv':csv},500,'uV')


@pytest.mark.parametrize('payload,match',[
    (npz_bytes(channels=CHANNELS[:-1]),'缺少'),
    (npz_bytes(channels=CHANNELS[:-1]+[CHANNELS[0]]),'重复'),
    (npz_bytes(sfreq=100),'采样率'),
    (npz_bytes(seconds=1),'不足'),
    (npz_bytes(eeg=np.full((500,62),np.nan)),'NaN'),
    (b'not an archive','格式'),
],ids=['missing-channel','duplicate-channel','sample-rate','too-short','nan','corrupt-zip'])
def test_invalid_inputs(payload,match):
    with pytest.raises(InputError,match=match):parse_recording({'x.npz':payload})


def test_set_fdt_and_event_boundaries():
    raw=np.random.default_rng(2).normal(size=(1000,62)).astype('<f4')
    meta=dict(nbchan=62,pnts=1000,trials=1,srate=250,data='original.fdt',datfile='',
              chanlocs=np.array([{'labels':c} for c in CHANNELS],dtype=object),
              event=np.array([{'latency':.5,'type':'boundary'},{'latency':1,'type':'stimulus'},{'latency':501,'type':'stimulus'}],dtype=object))
    b=io.BytesIO();savemat(b,meta)
    with pytest.raises(InputError,match='original.fdt'):parse_recording({'x.set':b.getvalue()},unit='uV')
    with pytest.raises(InputError,match='大小'):parse_recording({'x.set':b.getvalue(),'original.fdt':b'1234'},unit='uV')
    x,m=parse_recording({'x.set':b.getvalue(),'original.fdt':raw.tobytes()},unit='uV')
    np.testing.assert_array_equal(x,raw.T)
    assert m['boundaries']==[0.] and len(m['events'])==2
    f,t,a=extract(x,m,'events',['stimulus']);assert f.shape==(2,5,62)
    np.testing.assert_array_equal(t,[0,2])
    m['events'].append(dict(onset_sec=3,type='stimulus'))
    with pytest.raises(InputError,match='不足 2 秒'):extract(x,m,'events')


def test_missing_and_invalid_model(tmp_path,model_dir):
    assert not Engine(tmp_path/'missing').status()['ready']
    assert Engine(model_dir).status()['ready']
    manifest=json.loads((model_dir/'manifest.json').read_text());manifest['channels']=CHANNELS[::-1]
    (model_dir/'manifest.json').write_text(json.dumps(manifest))
    engine=Engine(model_dir);assert not engine.status()['ready'];assert '通道' in engine.error


def test_baseline_immutable_and_feature_time_causality(model_dir):
    model,_,_=Engine(model_dir).require();rng=np.random.default_rng(6)
    features=rng.normal(size=(35,5,62)).astype(np.float32)
    baseline=calibrate(model,features[:10],features[10:20],10)
    original={k:v.copy() for k,v in baseline.items()}
    params=dict(beta=.95,warmup=3,transition=.002,hysteresis=.8)
    first=predict(model,features[20:],baseline,np.arange(15)*2,params)
    changed=features[20:].copy();changed[8:]*=100
    second=predict(model,changed,baseline,np.arange(15)*2,params)
    assert first[:8]==second[:8]
    assert first==predict(model,features[20:],baseline,np.arange(15)*2,params)
    for key in baseline:np.testing.assert_array_equal(baseline[key],original[key])


def test_api_calibration_analysis_isolation_and_exports(tmp_path,model_dir):
    app=create_app(tmp_path/'runtime',model_dir)
    with TestClient(app) as c:
        def upload(seed,**extra):
            r=c.post('/api/recordings',files=[('files',('signal.npz',npz_bytes(seed,**extra)))])
            assert r.status_code==201,r.text;return r.json()
        a,b,q=upload(1),upload(2),upload(3,labels=np.ones(100))
        request=dict(record_0=a['id'],record_2=b['id'],subject='Tester',session='S1',shots=3)
        r=c.post('/api/calibrations',json={**request,'record_2':a['id']});assert r.status_code==422
        r=c.post('/api/calibrations',json=request);assert r.status_code==201,r.text
        profile=r.json();assert profile['query_statistics_used'] is False
        baseline=(tmp_path/'runtime/calibrations'/f"{profile['id']}.npz").read_bytes()
        query=dict(recording_id=q['id'],calibration_id=profile['id'],subject='Tester',session='S1')
        assert c.post('/api/analyses',json={**query,'recording_id':a['id']}).status_code==422
        duplicate=upload(1)
        assert c.post('/api/analyses',json={**query,'recording_id':duplicate['id']}).status_code==422
        assert c.post('/api/analyses',json={**query,'session':'S2'}).status_code==422
        assert c.delete('/api/recordings/'+a['id']).status_code==409
        def run(body):
            r=c.post('/api/analyses',json=body);assert r.status_code==202,r.text;key=r.json()['id']
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                job=c.get('/api/analyses/'+key).json()
                if job['status'] in ['complete','failed']:break
                time.sleep(.02)
            assert job['status']=='complete',job
            return job
        job=run(query);assert job['temporal_enabled'] is False and job['windows']==4
        assert job['final_class_label'] in [0,2]
        assert job['vote_0']+job['vote_2']==job['windows']
        assert job['final_rule'] in ['majority_vote','majority_vote_then_mean_probability']
        assert (tmp_path/'runtime/calibrations'/f"{profile['id']}.npz").read_bytes()==baseline
        q2=upload(3,labels=np.zeros(100))
        job2=run({**query,'recording_id':q2['id']});assert job['rows']==job2['rows']
        assert c.get('/api/analyses/'+job['id']+'/export').text.startswith('\ufeffwindow,')
        assert c.get('/api/analyses/'+job['id']+'/export?format=json').json()['mode']=='real'
        assert c.get('/api/recordings/not-a-uuid').status_code==404
        assert c.get('/api/status',headers={'Origin':'https://outside.example'}).status_code==403
        assert c.delete('/api/calibrations/'+profile['id']).status_code==200
        for item in [a,b,q,q2,duplicate]:assert c.delete('/api/recordings/'+item['id']).status_code==200
        for item in [job,job2]:assert c.delete('/api/analyses/'+item['id']).status_code==200


def test_weightless_api_cannot_classify(tmp_path):
    with TestClient(create_app(tmp_path/'runtime',tmp_path/'missing')) as c:
        assert c.get('/api/status').json()['model']['ready'] is False
        body=dict(recording_id='x',calibration_id='y',subject='A',session='S')
        assert c.post('/api/analyses',json=body).status_code==409
        assert c.post('/api/calibrations',json=dict(record_0='x',record_2='y',subject='A',session='S')).status_code==409
