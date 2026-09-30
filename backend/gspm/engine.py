"""Frozen-encoder inference with immutable calibration baselines."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from .network import EnhancedGatedSpectralChannelNet, gaussian_state, gaussian_probability, markov_filter, hysteresis_predictions
from .signal import FEATURE_CONFIG, CHANNELS, InputError


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def warmup_ema(probabilities, beta, warmup):
    running=np.zeros(2,dtype=np.float64); smoothed=None; output=[]
    for t,p in enumerate(np.asarray(probabilities,dtype=np.float64),1):
        if t <= warmup:
            running+=p; smoothed=running/t
        else:
            smoothed=beta*smoothed+(1-beta)*p
        output.append(smoothed.copy())
    return np.asarray(output)


def temporal_decision(probabilities, params):
    if not params:
        return probabilities.argmax(axis=1), probabilities
    evidence=warmup_ema(probabilities,params['beta'],params['warmup'])
    posterior=markov_filter(evidence,params['transition'],1.)
    return hysteresis_predictions(posterior,params['hysteresis']),posterior


class Engine:
    def __init__(self, model_dir):
        self.directory=Path(model_dir);self.model=None;self.config=None;self.version=None
        self.error='尚未放入模型权重与 manifest.json。';self.signature=None

    def refresh(self):
        paths=[self.directory/'manifest.json',self.directory/'encoder.pt']
        signature=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.is_file() else None for p in paths)
        if signature==self.signature:return
        self.signature=signature;self.model=None;self.config=None;self.version=None
        if not all(p.is_file() for p in paths):
            self.error='模型未就绪：需要 backend/models/encoder.pt 和 manifest.json。';return
        try:
            config=json.loads(paths[0].read_text(encoding='utf-8'))
            if config.get('architecture')!='EnhancedGatedSpectralChannelNet':raise ValueError('网络名称不匹配')
            if config.get('channels')!=CHANNELS:raise ValueError('62 通道顺序与当前网络不匹配')
            if digest(config.get('preprocessing'))!=digest(FEATURE_CONFIG):raise ValueError('预处理配置不匹配；请使用当前 manifest 模板')
            if config.get('class_labels') != [0,2]:raise ValueError('类别必须为 [0, 2]')
            temporal=config.get('temporal')
            if temporal:
                for key in ['beta','transition','hysteresis','warmup']:
                    if key not in temporal or not np.isfinite(temporal[key]):raise ValueError('时序参数不完整')
                if not (0 <= temporal['beta'] < 1 and 0 <= temporal['transition'] <= .5 and .5 <= temporal['hysteresis'] <= 1 and isinstance(temporal['warmup'],int) and temporal['warmup']>=1):raise ValueError('时序参数超出允许范围')
                if not temporal.get('validation_source'):raise ValueError('启用时序模块需填写独立验证参数来源')
            shape=config.get('encoder',{})
            if shape.get('feat_dim') != 96 or shape.get('hidden') != 128 or shape.get('dropout') != .25:raise ValueError('当前实现要求 feat_dim=96、hidden=128、dropout=0.25')
            # Never execute arbitrary pickle code from a checkpoint.
            saved=torch.load(paths[1],map_location='cpu',weights_only=True)
            if config.get('target_subject'):
                if saved.get('test_subject') != config['target_subject'] or saved.get('validation_subject') != config.get('validation_subject'):
                    raise ValueError('检查点目标/验证被试与 manifest 不匹配')
                if saved.get('ssl_source') != 'matb' or saved.get('nback_used_in_ssl') is not False:
                    raise ValueError('检查点不是目标协议的 MATB 自监督编码器')
                if saved.get('target_matb_used') is not False or saved.get('independent_validation_matb_used') is not False:
                    raise ValueError('检查点未满足目标/验证被试隔离')
            state=saved.get('model_state',saved)
            model=EnhancedGatedSpectralChannelNet(5,62,96,128,.25)
            model.load_state_dict(state,strict=True);model.eval()
            for param in model.parameters():
                if not torch.isfinite(param).all():raise ValueError('权重中存在无效数值')
                param.requires_grad_(False)
            self.version=hashlib.sha256(paths[1].read_bytes()+paths[0].read_bytes()).hexdigest()
            self.config=config;self.model=model;self.error=None
        except Exception as exc:
            self.error=f'模型加载失败：{str(exc)[:240]}'

    def require(self):
        self.refresh()
        if self.model is None:raise InputError(self.error)
        return self.model,self.version,self.config

    def status(self):
        self.refresh()
        return {'ready':self.model is not None,'message':self.error or 'GSC 编码器已就绪',
                'version':self.version,'label':(self.config or {}).get('name','未安装模型'),
                'temporal_ready':bool((self.config or {}).get('temporal')),'device':'CPU'}


class ModelCatalog:
    """Independent LOSO folds; never substitute a different held-out subject."""
    def __init__(self, model_root):
        root=Path(model_root)
        self.legacy=Engine(root)
        fold_root=root/'folds'
        self.folds={p.name:Engine(p) for p in sorted(fold_root.glob('sub-*')) if p.is_dir()}

    def status(self):
        if not self.folds:return {**self.legacy.status(),'folds':[],'mode':'single'}
        folds=[]
        for subject,engine in self.folds.items():
            status=engine.status()
            folds.append({'id':subject,'ready':status['ready'],'version':status['version'],
                          'temporal_ready':status['temporal_ready'],'message':status['message'],
                          'validation_subject':(engine.config or {}).get('validation_subject')})
        available=sum(row['ready'] for row in folds)
        return {'ready':available>0,'message':f'{available}/{len(folds)} 折模型通过严格校验；请选择对应目标被试。',
                'version':None,'label':'MATB-SSL GSC · 被试独立检查点',
                'temporal_ready':any(row['temporal_ready'] for row in folds),
                'device':'CPU','mode':'folds','folds':folds}

    def require(self, model_id=None, subject=None):
        if not self.folds:return self.legacy.require()
        if not model_id or model_id not in self.folds:
            raise InputError('请选择与目标被试对应的已安装模型折。')
        if subject in self.folds and subject!=model_id:
            raise InputError(f'{subject} 必须使用自身留出折 {subject}，不能选择 {model_id}。')
        model,version,config=self.folds[model_id].require()
        return model,version,config


def encode(model, X):
    outputs=[]
    with torch.inference_mode():
        for start in range(0,len(X),256):
            _,feature=model(torch.from_numpy(X[start:start+256].astype(np.float32)),return_features=True)
            outputs.append(feature.numpy())
    z=np.concatenate(outputs)
    if not np.isfinite(z).all():raise InputError('特征产生非有限值，请检查输入与模型。')
    return z


def calibrate(model,X0,X2,shots):
    if min(len(X0),len(X2))<shots:raise InputError(f'每类需要至少 {shots} 个有效窗口；目前为 {len(X0)} 和 {len(X2)}。')
    support=np.concatenate([X0[:shots],X2[:shots]])
    mean=support.mean(axis=0);scale=np.maximum(support.std(axis=0),1e-4)
    features=encode(model,(support-mean)/scale)
    labels=np.array([0]*shots+[1]*shots)
    means,variances=gaussian_state(features,labels,.35)
    return dict(mean=mean,scale=scale,means=means,variances=variances)


def predict(model,features,baseline,times,temporal=None,progress=None):
    z=encode(model,(features-baseline['mean'])/baseline['scale'])
    means=baseline['means'].copy();variances=baseline['variances'].copy();probabilities=[]
    for i,feature in enumerate(z):
        probability,_=gaussian_probability(feature,means,variances,.7)
        prediction=int(probability.argmax());probabilities.append(probability)
        old=means[prediction].copy()
        means[prediction]=.95*old+.05*feature
        variances[prediction]=np.maximum(.95*variances[prediction]+.05*(feature-old)**2,1e-4)
        if progress and i%30==0:progress(45+int(45*(i+1)/len(z)))
    probabilities=np.asarray(probabilities)
    labels,states=temporal_decision(probabilities,temporal)
    rows=[]
    for i,(time,p,label,state) in enumerate(zip(times,probabilities,labels,states)):
        rows.append({'window':i+1,'start_sec':round(float(time),4),'end_sec':round(float(time)+2,4),
            'prob_0':float(p[0]),'prob_2':float(p[1]),'raw_class':int(p.argmax())*2,
            'state_prob_0':float(state[0]),'state_prob_2':float(state[1]),'class_label':int(label)*2})
    return rows
