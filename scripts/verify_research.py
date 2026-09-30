"""Optional parity check against an untouched research-code folder."""
import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from scipy.io import savemat

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from gspm import network
from gspm.signal import CHANNELS,preprocess,welch_features
from gspm.engine import calibrate,predict,temporal_decision


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value
    spec.loader.exec_module(value);return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('research_root',type=Path)
    args=parser.parse_args();source=args.research_root
    old_dir=source/'outputs/30_自监督增强Gated_原型DOSA/01_代码'
    sys.path.insert(0,str(old_dir))
    common=module('ssl_common',old_dir/'ssl_common.py')
    old=module('reference_prototype',old_dir/'ssl_gated_source_pretrain.py')
    raw_module=module('reference_raw',old_dir/'01_preprocess_raw_eeg.py')
    psd_module=module('reference_psd',source/'outputs/03_脚本与工具/01_训练脚本/train_cog_nback_0v2_welch_center_loso.py')
    temporal=module('reference_temporal',source/'outputs/37_MATB跨任务自监督预训练/01_代码/03_evaluate_nback_fewshot_gspm.py')
    torch.set_num_threads(4);torch.manual_seed(2026);rng=np.random.default_rng(2026)
    errors={}
    def check(name,a,b,atol=1e-6):
        errors[name]=float(np.max(np.abs(np.asarray(a)-np.asarray(b))))
        np.testing.assert_allclose(a,b,atol=atol,rtol=1e-5,err_msg=name)
    raw=rng.normal(size=(62,6000)).astype(np.float32);raw[0]*=100
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp);raw.T.astype('<f4').tofile(temp/'sample.fdt')
        savemat(temp/'sample.set',dict(nbchan=62,pnts=6000,trials=1,srate=500,data='sample.fdt',datfile='sample.fdt',chanlocs=np.array([{'labels':c} for c in CHANNELS],dtype=object)))
        rec=raw_module.Recording('T','S','0-back',0,temp/'sample.set')
        options=SimpleNamespace(bad_channel_z=8.,notch_freq=50.,l_freq=1.,h_freq=40.,clip_z=10.,resample_freq=250.)
        expected,_,_,_=raw_module.preprocess(rec,CHANNELS,options)
        actual,_=preprocess(raw,500);check('preprocessing',actual,expected,0)
    options=SimpleNamespace(epoch_sec=2.,welch_window_sec=1.,welch_overlap_sec=.5,feature_mode='logpower',band_set='five40')
    starts=[0,500,1000,1500]
    reference,_=psd_module.extract_welch_bandpower_epochs(expected,250,options,starts)
    check('welch_log_psd',welch_features(actual,starts),reference,0)
    m=network.EnhancedGatedSpectralChannelNet(5,62,96,128,.25).eval()
    ref_model=common.EnhancedGatedSpectralChannelNet(5,62,96,128,.25).eval();ref_model.load_state_dict(m.state_dict())
    x=rng.normal(size=(35,5,62)).astype(np.float32)
    with torch.inference_mode():
        check('gsc_features',m(torch.from_numpy(x),True)[1],ref_model(torch.from_numpy(x),True)[1],0)
    baseline=calibrate(m,x[:10],x[10:20],10)
    standardized=(x-baseline['mean'])/baseline['scale'];y=np.array([0]*10+[1]*10+[0]*15)
    params=json.loads((source/'configs/当前论文协议/03_Nback最终评估参数.json').read_text(encoding='utf-8'))
    trace=old.run_prototype_dosa_session(ref_model,standardized,y,np.arange(20),np.arange(20,35),torch.device('cpu'),SimpleNamespace(**params))
    actual=predict(m,x[20:],baseline,np.arange(15)*2)
    check('prototype_scores',[[r['prob_0'],r['prob_2']] for r in actual],[[r['prob_0'],r['prob_1']] for r in trace],2e-6)
    p=np.array([[r['prob_0'],r['prob_2']] for r in actual]);settings=dict(beta=.95,warmup=3,transition=.002,hysteresis=.8)
    label,state=temporal_decision(p,settings)
    ref_label,ref_state=temporal.state_decision(p,settings,use_ema=True,use_markov=True)
    check('temporal_posterior',state,ref_state,0);check('temporal_labels',label,ref_label,0)
    report=dict(seed=2026,weights='random fixture only',maximum_absolute_errors=errors,passed=True)
    (ROOT/'docs/numerical-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
