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


def prepared_bytes(role='query',subject='sub-01',session='ses-S1',source='source0',onsets=None,seed=1):
    onsets=onsets if onsets is not None else ([20.,22.,24.] if role=='query' else [0.,2.,4.])
    manifest=dict(subject=subject,session=session,role=role,source_id=source,onsets=onsets,feature_config=FEATURE_CONFIG)
    return npz_bytes(seed,seconds=2*len(onsets),processing_stage='gspm_preprocessed_epochs_v1',
                     prepared_manifest=json.dumps(manifest))


@pytest.fixture
def auto_environment(tmp_path,model_dir):
    import shutil
    models=tmp_path/'catalog';fold=models/'folds/sub-01';fold.mkdir(parents=True)
    config=json.loads((model_dir/'manifest.json').read_text())
    config.update(target_subject='sub-01',validation_subject='sub-02')
    (fold/'manifest.json').write_text(json.dumps(config))
    state=torch.load(model_dir/'encoder.pt',weights_only=True)
    torch.save(dict(model_state=state,test_subject='sub-01',validation_subject='sub-02',ssl_source='matb',
                    nback_used_in_ssl=False,target_matb_used=False,independent_validation_matb_used=False),fold/'encoder.pt')
    support=tmp_path/'support';folder=support/'sub-01/ses-S1';folder.mkdir(parents=True)
    (folder/'support0.npz').write_bytes(prepared_bytes('support0',seed=2))
    (folder/'support2.npz').write_bytes(prepared_bytes('support2',source='source2',seed=3))
    data=tmp_path/'data'
    return data,models,support


def upload_prepared(client,content=None,name='sub-01_ses-S1_查询_2-back.npz'):
    r=client.post('/api/recordings',files=[('files',(name,content or prepared_bytes()))])
    assert r.status_code==201,r.text
    return r.json()['id']


def test_auto_prepare_reuses_and_matches_manual_results(auto_environment):
    data,models,support=auto_environment
    with TestClient(create_app(data,models,support)) as c:
        query=upload_prepared(c)
        endpoint=f'/api/recordings/{query}/prepare-analysis'
        r=c.post(endpoint);assert r.status_code==200,r.text
        ready=r.json();assert ready['model_id']=='sub-01' and ready['validation_subject']=='sub-02'
        profile=c.get('/api/calibrations').json()[0];assert profile['shots']==3
        assert c.post(endpoint).json()==ready
        assert len(c.get('/api/recordings').json())==3 and len(c.get('/api/calibrations').json())==1
        # An unrelated filename class label must never change inference inputs.
        renamed=upload_prepared(c,name='sub-01_ses-S1_查询_0-back.npz')
        assert c.post(f'/api/recordings/{renamed}/prepare-analysis').json()==ready
        def run(calibration_id):
            payload={k:ready[k] for k in ['subject','session','window_mode','event_types']}
            job=c.post('/api/analyses',json={**payload,'recording_id':query,'calibration_id':calibration_id})
            assert job.status_code==202,job.text
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                result=c.get('/api/analyses/'+job.json()['id']).json()
                if result['status'] in ['complete','failed']:break
                time.sleep(.02)
            assert result['status']=='complete',result
            return result['rows']
        rows=run(ready['calibration_id'])
        manual=c.post('/api/calibrations',json=dict(record_0=profile['recording_ids'][0],record_2=profile['recording_ids'][1],
            subject='sub-01',session='ses-S1',model_id='sub-01',shots=3)).json()
        assert rows==run(manual['id'])
        assert c.post(endpoint).json()['calibration_id']==manual['id']
        # Existing valid profiles work even if the local support directory is gone.
        import shutil
        shutil.rmtree(support)
        assert c.post(endpoint).json()['calibration_id']==manual['id']


@pytest.mark.parametrize('change,message',[
    ('missing_support','缺失'),('duplicate','重复'),('source','来源'),('overlap','重叠'),
    ('counts','数量'),('identity','不一致')])
def test_auto_prepare_rejects_invalid_local_support(auto_environment,change,message):
    data,models,support=auto_environment;folder=support/'sub-01/ses-S1'
    if change=='missing_support':(folder/'support2.npz').unlink()
    elif change=='duplicate':(folder/'copy.npz').write_bytes((folder/'support0.npz').read_bytes())
    elif change=='source':(folder/'support0.npz').write_bytes(prepared_bytes('support0',source='other',seed=2))
    elif change=='overlap':(folder/'support0.npz').write_bytes(prepared_bytes('support0',onsets=[20.,22.,24.],seed=2))
    elif change=='counts':(folder/'support2.npz').write_bytes(prepared_bytes('support2',source='source2',onsets=[0.,2.],seed=3))
    elif change=='identity':(folder/'support0.npz').write_bytes(prepared_bytes('support0',session='ses-S2',seed=2))
    with TestClient(create_app(data,models,support)) as c:
        query=upload_prepared(c);r=c.post(f'/api/recordings/{query}/prepare-analysis')
        assert r.status_code==409,r.text;assert message in r.json()['detail']
        assert not c.get('/api/calibrations').json()
        assert len(c.get('/api/recordings').json())==1


@pytest.mark.parametrize('content,name,code,message',[
    (npz_bytes(),'plain.npz',422,'专用'),
    (prepared_bytes('support0'),'support.npz',422,'查询'),
    (prepared_bytes(),'sub-05_ses-S1_query.npz',422,'不一致'),
    (prepared_bytes(),'sub-01_ses-S2_query.npz',422,'不一致'),
    (prepared_bytes(subject='sub-99'),'query.npz',409,'模型折'),
    (prepared_bytes(session='../../outside'),'query.npz',422,'标识')],
    ids=['plain-npz','support-as-query','wrong-subject-name','wrong-session-name','missing-fold','unsafe-session'])
def test_auto_prepare_rejects_query_identity(auto_environment,content,name,code,message):
    with TestClient(create_app(*auto_environment)) as c:
        query=upload_prepared(c,content,name);r=c.post(f'/api/recordings/{query}/prepare-analysis')
        assert r.status_code==code,r.text;assert message in r.json()['detail']


@pytest.mark.parametrize('damage',['version','preprocessing','baseline','record','overlap'])
def test_auto_prepare_ignores_invalid_existing_profiles(auto_environment,damage):
    data,models,support=auto_environment
    with TestClient(create_app(data,models,support)) as c:
        query=upload_prepared(c);endpoint=f'/api/recordings/{query}/prepare-analysis'
        first=c.post(endpoint).json();key=first['calibration_id'];file=data/'calibrations'/f'{key}.json'
        profile=json.loads(file.read_text())
        if damage=='version':profile['model_version']='obsolete'
        elif damage=='preprocessing':profile['preprocessing']={}
        elif damage=='baseline':(data/'calibrations'/f'{key}.npz').write_bytes(b'invalid')
        elif damage=='record':(data/'recordings'/f"{profile['recording_ids'][0]}.npy").unlink()
        elif damage=='overlap':profile['prepared_support'][0]['onsets']=[20.,22.,24.]
        file.write_text(json.dumps(profile))
        replacement=c.post(endpoint);assert replacement.status_code==200,replacement.text
        assert replacement.json()['calibration_id']!=key
        assert c.post(endpoint).json()==replacement.json()
