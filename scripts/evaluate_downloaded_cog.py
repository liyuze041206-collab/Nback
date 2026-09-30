"""Offline six-subject evaluation using the supplied, untouched research functions.

Run with the project's .venv Python. No training, web service, or network access.
The original research directories remain dependencies and are never written to.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
import torch

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parents[1]
RESEARCH = ROOT / '已有材料/代码和权重'
OUTPUTS = RESEARCH / 'outputs'
OLD = OUTPUTS / '30_自监督增强Gated_原型DOSA/01_代码'
EXPERIMENT = OUTPUTS / '37_MATB跨任务自监督预训练'
SUBJECTS = ['sub-01', 'sub-05', 'sub-09', 'sub-13', 'sub-18', 'sub-23']
PAIRING = dict(zip(SUBJECTS, ['sub-02', 'sub-06', 'sub-10', 'sub-14', 'sub-19', 'sub-24']))
PRE_OPTIONS = dict(bad_channel_z=8., notch_freq=50., l_freq=1., h_freq=40., clip_z=10., resample_freq=250.)
PSD_OPTIONS = dict(epoch_sec=2., welch_window_sec=1., welch_overlap_sec=.5, feature_mode='logpower', band_set='five40')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


sys.path.insert(0, str(OLD))
raw = module('cog_original_raw', OLD / '01_preprocess_raw_eeg.py')
psd = module('cog_original_psd', OUTPUTS / '03_脚本与工具/01_训练脚本/train_cog_nback_0v2_welch_center_loso.py')
evaluation = module('cog_original_eval', EXPERIMENT / '01_代码/03_evaluate_nback_fewshot_gspm.py')
sys.path.insert(0, str(CODE / 'backend'))
from gspm import signal as app_signal, engine as app_engine, network as app_network


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def csv(path, frame):
    frame.to_csv(path, index=False, encoding='utf-8-sig', float_format='%.12g')


def check(name, actual, expected, checks, atol=0., rtol=0.):
    a, b = np.asarray(actual), np.asarray(expected)
    error = float(np.max(np.abs(a.astype(float) - b.astype(float)))) if a.size else 0.
    np.testing.assert_allclose(a, b, atol=atol, rtol=rtol, err_msg=name)
    checks.append(dict(check=name, passed=True, max_absolute_error=error, atol=atol, rtol=rtol))


def discover(data_root):
    records = []
    for task, (filename, nback) in raw.TASK_FILES.items():
        for path in data_root.rglob(filename):
            if path.parent.name != 'eeg':
                continue
            subject, session = path.parents[2].name, path.parents[1].name
            if subject in SUBJECTS:
                records.append(raw.Recording(subject, session, task, nback, path.resolve()))
    keys = [(r.subject, r.session, r.task) for r in records]
    expected = {(s, session, task) for s in SUBJECTS for session in ['ses-S1', 'ses-S2', 'ses-S3'] for task in raw.TASK_FILES}
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError(f'Duplicate or incomplete recordings; missing={expected-set(keys)}, found={len(keys)}')
    return sorted(records, key=lambda r: (r.subject, r.session, r.task))


def validate_record(record, channels):
    meta = raw.load_set_metadata(record.set_path)
    names = meta['ch_names']
    if len(names) != meta['nbchan'] or len(names) != len(set(names)) or not all(names):
        raise ValueError(f'Invalid/duplicate channels: {record.set_path}')
    if set(channels) - set(names):
        raise ValueError(f'Missing channels: {set(channels)-set(names)}')
    if meta['sfreq'] != 500. or meta['trials'] != 1 or meta['pnts'] < 1000:
        raise ValueError(f'Expected continuous 500 Hz recording >=2 s: {record.set_path}')
    fdt = meta['fdt_path'].resolve()
    if fdt.parent != record.set_path.parent.resolve() or not fdt.is_file():
        raise ValueError(f'Missing or invalid paired FDT: {fdt}')
    data = raw.read_fdt(meta)
    if not np.isfinite(data).all():
        raise ValueError(f'NaN/Inf in raw EEG: {fdt}')
    selected = data[[names.index(c) for c in channels]]
    scales = raw.robust_sigma(selected, axis=1)
    bad = np.flatnonzero((scales > np.median(scales)*8) | (scales < np.median(scales)/8))
    if len(bad) >= len(channels)//2:
        raise ValueError(f'Too many bad channels for original repair rule: {record.set_path}')
    return meta, dict(subject=record.subject, session=record.session, task=record.task,
                     true_label=record.nback, source=str(record.set_path), fdt_path=str(fdt),
                     set_sha256=sha(record.set_path), fdt_sha256=sha(fdt),
                     raw_channels=len(names), samples=meta['pnts'], raw_sfreq=meta['sfreq'],
                     duration_sec=meta['pnts']/meta['sfreq'], finite_raw=True,
                     excluded_channels=';'.join(n for n in names if n not in channels))


def event_windows(record, meta, samples):
    rows, excluded, boundaries = [], [], []
    for event in raw.as_list(meta['events']):
        if str(getattr(event, 'type', '')) == 'boundary':
            latency = float(getattr(event, 'latency', np.nan))
            if np.isfinite(latency):
                boundaries.append((latency-1)/meta['sfreq'])
    for position, event in enumerate(raw.as_list(meta['events'])):
        code = str(getattr(event, 'type', ''))
        if code not in psd.STIM_EVENT_TYPES[record.nback]:
            continue
        latency = float(getattr(event, 'latency', np.nan))
        onset = (latency-1)/meta['sfreq']
        base = dict(subject=record.subject, session=record.session, task=record.task,
                    source=str(record.set_path), event_index=position, event_type=code,
                    original_latency_1based=latency, original_onset_sec=onset, true_label=record.nback)
        if not np.isfinite(onset):
            raise ValueError(f'Nonfinite stimulus event: {base}')
        start = int(round(onset*250))
        reason = 'out_of_bounds' if start < 0 or start+500 > samples else None
        if any(onset < boundary < onset+2 for boundary in boundaries):
            reason = 'crosses_boundary'
        if reason:
            excluded.append(dict(**base, reason=reason))
        else:
            rows.append(dict(**base, start_sample_250hz=start, epoch_start_sec=start/250., epoch_end_sec=start/250.+2))
    rows.sort(key=lambda r: (r['start_sample_250hz'], r['event_index']))
    starts = [r['start_sample_250hz'] for r in rows]
    if not starts or len(set(starts)) != len(starts):
        raise ValueError(f'Empty or duplicate stimulus windows: {record.set_path}')
    return rows, excluded


def preprocess_all(records, channels, out, reuse, checks, algorithm_hashes):
    manifest, windows, excluded, features = [], [], [], []
    for position, record in enumerate(records, 1):
        meta, info = validate_record(record, channels)
        stem = f'{record.subject}_{record.session}_{record.task}'
        saved = out / 'preprocessed' / f'{stem}.npz'
        sidecar = saved.with_suffix('.json')
        fingerprint = dict(set_sha256=info['set_sha256'], fdt_sha256=info['fdt_sha256'],
                           options=PRE_OPTIONS, channels=channels, algorithm_hashes=algorithm_hashes)
        if reuse and saved.exists() and sidecar.exists():
            prior = json.loads(sidecar.read_text(encoding='utf-8'))
            if prior['fingerprint'] != fingerprint or prior['output_sha256'] != sha(saved):
                raise ValueError(f'Cached preprocessing mismatch: {saved}; rerun without --reuse-preprocessed')
            with np.load(saved, allow_pickle=False) as z:
                processed, sfreq = z['data'], float(z['sfreq'])
            bad = prior['bad_indices']
        else:
            processed, sfreq, _, bad = raw.preprocess(record, channels, SimpleNamespace(**PRE_OPTIONS))
            if not np.isfinite(processed).all() or sfreq != 250. or processed.shape[0] != 62:
                raise ValueError(f'Invalid preprocessed array: {stem}')
            # No arbitrary unit scaling: preserve native EEGLAB values, as the paired research code does.
            np.savez_compressed(saved, data=processed, sfreq=sfreq, ch_names=np.asarray(channels),
                                subject=record.subject, session=record.session, task=record.task,
                                nback=record.nback, processing_stage='preprocessed_continuous',
                                amplitude_convention='native_EEGLAB_no_rescaling')
            dump(sidecar, dict(fingerprint=fingerprint, bad_indices=bad, output_sha256=sha(saved)))
        rows, rejected = event_windows(record, meta, processed.shape[1])
        starts = [r['start_sample_250hz'] for r in rows]
        X, times = psd.extract_welch_bandpower_epochs(processed, sfreq, SimpleNamespace(**PSD_OPTIONS), starts)
        if X.shape != (len(rows), 5, 62) or not np.isfinite(X).all():
            raise ValueError(f'Invalid feature array: {stem}')
        check(stem+'/welch_reference_vs_app', app_signal.welch_features(processed, starts), X, checks)
        if position == 1:
            input_data = raw.read_fdt(meta)[[meta['ch_names'].index(c) for c in channels]]
            alternative, alternative_bad = app_signal.preprocess(input_data, meta['sfreq'])
            check(stem+'/preprocessing_reference_vs_app', alternative, processed, checks)
            if list(bad) != list(alternative_bad):
                raise AssertionError('Bad channel repair mismatch')
            del input_data, alternative
        for row, epoch_time in zip(rows, times):
            row['sample_index'] = len(windows)
            # Keep original float32 reference times for exact support/replay ordering.
            row['epoch_start_sec'] = float(epoch_time)
            row['epoch_end_sec'] = float(epoch_time)+2
            windows.append(row)
        excluded.extend(rejected)
        features.append(X)
        raw.export_events(record, meta, out / 'events' / f'{stem}.csv')
        info.update(preprocessed_path=str(saved), processed_samples=processed.shape[1], processed_sfreq=sfreq,
                    valid_windows=len(rows), excluded_windows=len(rejected), bad_channels=';'.join(channels[i] for i in bad),
                    preprocessed_sha256=sha(saved))
        manifest.append(info)
        csv(out/'input_manifest.csv', pd.DataFrame(manifest))
        print(f'[{position}/36] {stem}: windows={len(rows)} repaired={len(bad)} excluded={len(rejected)}', flush=True)
    frame = pd.DataFrame(windows)
    X = np.concatenate(features)
    np.savez_compressed(out/'features/all_windows.npz', X=X, y=(frame.true_label.to_numpy()//2),
                        subject=frame.subject.to_numpy(dtype=str), session=frame.session.to_numpy(dtype=str),
                        task=frame.task.to_numpy(dtype=str), source=frame.source.to_numpy(dtype=str),
                        bands=np.asarray([b[0] for b in app_signal.BANDS]), keep_channels=np.asarray(channels),
                        epoch_start_sec=frame.epoch_start_sec.to_numpy(), true_label=frame.true_label.to_numpy(),
                        sample_index=frame.sample_index.to_numpy(), processing_stage='welch_log_psd')
    csv(out/'excluded_windows.csv', pd.DataFrame(excluded, columns=list(frame.columns)+['reason']))
    return evaluation.common.load_cache(out/'features/all_windows.npz'), frame, pd.DataFrame(manifest)


def score(labels, predictions):
    labels, predictions = np.asarray(labels), np.asarray(predictions)
    cm = confusion_matrix(labels, predictions, labels=[0, 2])
    accuracy = float((labels == predictions).mean())
    if not np.isclose(accuracy, np.trace(cm)/cm.sum(), atol=1e-15):
        raise AssertionError('Independent confusion-matrix accuracy mismatch')
    return dict(n_query=len(labels), n_correct=int((labels == predictions).sum()), accuracy=accuracy,
                precision_macro=float(precision_score(labels, predictions, labels=[0,2], average='macro', zero_division=0)),
                recall_macro=float(recall_score(labels, predictions, labels=[0,2], average='macro', zero_division=0)),
                f1_macro=float(f1_score(labels, predictions, labels=[0,2], average='macro', zero_division=0)),
                true0_pred0=int(cm[0,0]), true0_pred2=int(cm[0,1]), true2_pred0=int(cm[1,0]), true2_pred2=int(cm[1,1]))


def verify_block(model, mirror, data, Xz, support, ordered, args, params, ref, checks, name):
    """Fixed support/query membership: query truth may only affect the exported y_true."""
    old = evaluation.old
    p = ref[['prob_0', 'prob_1']].to_numpy()
    rerun = pd.DataFrame(old.run_prototype_dosa_session(model, Xz, data['y'], support, ordered, torch.device('cpu'), args))
    check(name+'/repeat_probabilities', rerun[['prob_0','prob_1']], p, checks)
    labels = data['y'].copy()
    labels[ordered] = 1-labels[ordered]
    changed = pd.DataFrame(old.run_prototype_dosa_session(model, Xz, labels, support, ordered, torch.device('cpu'), args))
    check(name+'/query_truth_independence', changed.drop(columns=['y_true','correction_mode']).to_numpy(dtype=float),
          rerun.drop(columns=['y_true','correction_mode']).to_numpy(dtype=float), checks)
    # Cut within a five-window chunk, to detect inadvertent within-chunk look-ahead.
    cut = min(17, len(ordered)//2)
    altered = Xz.copy()
    altered[ordered[cut:]] = altered[ordered[cut:]] * -2.7 + 4.2
    future = pd.DataFrame(old.run_prototype_dosa_session(model, altered, data['y'], support, ordered, torch.device('cpu'), args))
    check(name+'/future_features_do_not_change_prefix', future[['prob_0','prob_1']].to_numpy()[:cut], p[:cut], checks)
    pred, posterior = evaluation.state_decision(p, params, use_ema=True, use_markov=True)
    pred_future, post_future = evaluation.state_decision(future[['prob_0','prob_1']].to_numpy(), params, use_ema=True, use_markov=True)
    check(name+'/temporal_prefix_labels', pred_future[:cut], pred[:cut], checks)
    check(name+'/temporal_prefix_posterior', post_future[:cut], posterior[:cut], checks)
    other_pred, other_post = app_engine.temporal_decision(p, params)
    check(name+'/temporal_reference_vs_app', other_post, posterior, checks)
    check(name+'/temporal_labels_reference_vs_app', other_pred, pred, checks)
    s0, s2 = [support[data['y'][support] == cls] for cls in (0,1)]
    baseline = app_engine.calibrate(mirror, data['X'][s0], data['X'][s2], len(s0))
    result = app_engine.predict(mirror, data['X'][ordered], baseline, data['epoch_start_sec'][ordered])
    other_p = np.asarray([[r['prob_0'], r['prob_2']] for r in result])
    check(name+'/prototype_reference_vs_app', other_p, p, checks, atol=3e-5, rtol=1e-5)
    check(name+'/prototype_labels_reference_vs_app', other_p.argmax(1), p.argmax(1), checks)


def evaluate_all(data, windows, out, channels, checks):
    historical = pd.read_csv(EXPERIMENT/'13_Nback_24epoch/fold_metrics.csv')
    config = json.loads((EXPERIMENT/'13_Nback_24epoch/config.json').read_text(encoding='utf-8'))
    args = SimpleNamespace(**config)
    torch.set_num_threads(4)
    torch.manual_seed(2026)
    torch.use_deterministic_algorithms(True)
    metrics_rows, prediction_frames, model_audit, norm_frames = [], [], [], []
    windows['split'] = 'query'
    for subject in SUBJECTS:
        saved_row = historical.loc[historical.target_subject == subject].iloc[0]
        if saved_row.validation_subject != PAIRING[subject]:
            raise AssertionError('Original fold pairing mismatch')
        checkpoint_path = EXPERIMENT/f'12_MATB_SSL_24epoch/checkpoints/matb_ssl_enhanced_gsc_{subject}.pt'
        cp = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
        if (cp['test_subject'] != subject or cp['validation_subject'] != PAIRING[subject]
            or cp['source_subject_count'] != 24 or cp['target_matb_used'] or cp['independent_validation_matb_used']
            or cp['nback_used_in_ssl'] or subject in cp['source_subjects'] or PAIRING[subject] in cp['source_subjects']):
            raise AssertionError('Checkpoint source/target isolation failed')
        local_manifest = CODE/f'backend/models/folds/{subject}/manifest.json'
        manifest = json.loads(local_manifest.read_text(encoding='utf-8'))
        if manifest['channels'] != channels or manifest['preprocessing'] != json.loads(json.dumps(app_signal.FEATURE_CONFIG)):
            raise AssertionError('Checkpoint input configuration mismatch')
        params = {k: float(saved_row['selected_'+k]) for k in ['beta','transition','hysteresis']}
        params['warmup'] = int(saved_row.selected_warmup)
        if any(manifest['temporal'][k] != v for k,v in params.items()):
            raise AssertionError('Saved temporal settings differ between original CSV and manifest')
        model = evaluation.load_model(cp, cp, data['X'].shape, torch.device('cpu'))
        if any(not torch.isfinite(p).all() for p in model.parameters()):
            raise ValueError('Nonfinite checkpoint weights')
        mirror = app_network.EnhancedGatedSpectralChannelNet(5,62,cp['feat_dim'],cp['hidden'],cp['dropout']).eval()
        mirror.load_state_dict(cp['model_state'], strict=True)
        own = np.flatnonzero(data['subjects'] == subject)
        support, query = evaluation.common.split_calibration(own, data['y'],20,2026+int(subject[-2:]),
                    sessions=data['sessions'], epoch_start_sec=data['epoch_start_sec'], mode='session_start')
        if len(support) != 40 or np.intersect1d(support,query).size or len(support)+len(query) != len(own):
            raise AssertionError('Invalid support/query membership')
        windows.loc[support, 'split'] = 'support'
        Xz, norm_audit = evaluation.support_only_standardize(data['X'], data['subjects'], data['sessions'], support, subject)
        norm_frames.extend(norm_audit)
        fold_dir = out/'results/folds'/subject
        fold_dir.mkdir(parents=True, exist_ok=True)
        with torch.inference_mode():
            fixed = torch.from_numpy(Xz[own[:16]])
            check(subject+'/gsc_reference_vs_app', mirror(fixed,return_features=True)[1].numpy(),
                  model(fixed,return_features=True)[1].numpy(), checks)
        changed_X = data['X'].copy()
        changed_X[query] += 123.45
        changed_z, _ = evaluation.support_only_standardize(changed_X,data['subjects'],data['sessions'],support,subject)
        check(subject+'/support_normalization_query_independence',changed_z[support],Xz[support],checks)
        for session, shots in zip(['ses-S1','ses-S2','ses-S3'],[7,7,6]):
            subset = support[data['sessions'][support] == session]
            if [int(np.sum(data['y'][subset] == cls)) for cls in [0,1]] != [shots,shots]:
                raise AssertionError('Support allocation must be 7/7/6 per class')
            mean = data['X'][subset].mean(0)
            scale = np.maximum(data['X'][subset].std(0),1e-4)
            feat = evaluation.old.extract_features(model,Xz,subset,256,torch.device('cpu'))
            means, variances = evaluation.old.gaussian_state(feat,data['y'][subset],.35)
            np.savez_compressed(fold_dir/f'{session}_calibration.npz',mean=mean,scale=scale,
                                prototype_means=means,prototype_variances=variances,support_indices=subset,
                                checkpoint_sha256=sha(checkpoint_path),channels=np.asarray(channels))
        trace, support_counts, normalization = evaluation.build_trace(model,data,subject,int(subject[-2:]),args,torch.device('cpu'))
        final_score, predictions = evaluation.evaluate_trace(trace,params,use_ema=True,use_markov=True,return_predictions=True)
        ema_score, _ = evaluation.evaluate_trace(trace,params,use_ema=True,use_markov=False)
        csv(fold_dir/'reference_raw_trace.csv',trace)
        csv(fold_dir/'support_counts.csv',support_counts)
        csv(fold_dir/'normalization_audit.csv',normalization)
        dump(fold_dir/'temporal_parameters.json',params)
        for block, frame in trace.groupby('task_block_id',sort=True):
            frame = frame.sort_values(['epoch_start_sec','sample_index'])
            ordered = frame.sample_index.to_numpy(dtype=int)
            subset = support[data['sessions'][support] == frame.session.iloc[0]]
            # Check actual interval overlap, not only different row identifiers.
            own_support = subset[data['sources'][subset] == frame.source.iloc[0]]
            if np.any(data['epoch_start_sec'][own_support]+2 > data['epoch_start_sec'][ordered].min()+1e-5):
                raise AssertionError('Support/query time intervals overlap')
            verify_block(model,mirror,data,Xz,subset,ordered,args,params,frame,checks,f'{subject}/{frame.session.iloc[0]}/{frame.task.iloc[0]}')
        raw_scores = score(trace.y_true.to_numpy()*2,trace.y_pred.to_numpy()*2)
        final_scores = score(predictions.y_true.to_numpy()*2,predictions.y_pred.to_numpy()*2)
        check(subject+'/summary_reference_accuracy',final_scores['accuracy'],final_score['accuracy'],checks,atol=1e-15)
        merged = windows.merge(predictions[['sample_index','y_pred','state_prob_0','state_prob_2']],on='sample_index',validate='one_to_one')
        merged = merged.merge(trace[['sample_index','y_pred','prob_0','prob_1','updated']],on='sample_index',suffixes=('_final','_raw'),validate='one_to_one')
        merged['predicted_label'] = merged.pop('y_pred_final')*2
        merged['raw_predicted_label'] = merged.pop('y_pred_raw')*2
        merged.rename(columns={'prob_1':'prob_2'},inplace=True)
        merged['correct'] = merged.true_label == merged.predicted_label
        merged['model_subject'] = subject
        merged['checkpoint_sha256'] = sha(checkpoint_path)
        merged['model_version'] = sha(checkpoint_path)[:16]
        csv(fold_dir/'window_predictions.csv',merged)
        prediction_frames.append(merged)
        metrics_rows.append(dict(subject=subject,validation_subject=PAIRING[subject],n_support=len(support),
                                 raw_accuracy=raw_scores['accuracy'],raw_f1_macro=raw_scores['f1_macro'],
                                 ema_accuracy=ema_score['accuracy'],ema_f1_macro=ema_score['f1_macro'],
                                 historical_accuracy=float(saved_row.accuracy),
                                 difference_from_historical=final_scores['accuracy']-float(saved_row.accuracy),**final_scores))
        model_audit.append(dict(subject=subject,validation_subject=PAIRING[subject],checkpoint=str(checkpoint_path),
                                checkpoint_sha256=sha(checkpoint_path),manifest_sha256=sha(local_manifest),
                                temporal=params,selection='saved independent-validation parameters; no reselection',
                                source_subject_count=24,source_subjects=cp['source_subjects'],
                                query_labels_used_for_prediction=False,trainable_encoder_parameters=0))
        print(f'{subject}: raw={raw_scores["accuracy"]:.4%} EMA={ema_score["accuracy"]:.4%} final={final_scores["accuracy"]:.4%}; verification passed',flush=True)
        dump(out/'verification.json',dict(passed=True,scope='completed subjects so far',checks=checks))
    final = pd.concat(prediction_frames,ignore_index=True)
    folds = pd.DataFrame(metrics_rows)
    csv(out/'features/window_manifest.csv',windows)
    csv(out/'features/support_windows.csv',windows[windows.split == 'support'])
    csv(out/'features/query_windows.csv',windows[windows.split == 'query'])
    csv(out/'results/window_predictions.csv',final)
    csv(out/'results/subject_metrics.csv',folds)
    csv(out/'results/normalization_audit.csv',pd.DataFrame(norm_frames))
    dump(out/'model_audit.json',model_audit)
    return final, folds, windows


def write_report(out, final, folds, windows, manifest, checks, run):
    all_score = score(final.true_label,final.predicted_label)
    summary = dict(subjects=SUBJECTS,recordings=len(manifest),valid_windows=len(windows),
                   support_windows=int((windows.split=='support').sum()),query_windows=len(final),
                   excluded_windows=int(manifest.excluded_windows.sum()),pooled=all_score,
                   subject_mean_accuracy=float(folds.accuracy.mean()),subject_sd_accuracy=float(folds.accuracy.std(ddof=1)),
                   subject_mean_macro_f1=float(folds.f1_macro.mean()),subject_sd_macro_f1=float(folds.f1_macro.std(ddof=1)),
                   raw_pooled=score(final.true_label,final.raw_predicted_label),
                   limitation='Six downloaded subjects; offline few-shot replay, not full 26-subject replication or clinical confidence.')
    dump(out/'results/summary.json',summary)
    sessions = [dict(subject=s,session=t,**score(g.true_label,g.predicted_label)) for (s,t),g in final.groupby(['subject','session'])]
    csv(out/'results/session_metrics.csv',pd.DataFrame(sessions))
    classes = []
    for subject, group in [('ALL',final),*list(final.groupby('subject'))]:
        for label in [0,2]:
            truth, prediction = group.true_label == label, group.predicted_label == label
            tp = int((truth & prediction).sum())
            classes.append(dict(subject=subject,true_label=label,n_true=int(truth.sum()),n_predicted=int(prediction.sum()),
                                n_correct=tp,recall=tp/int(truth.sum()),precision=tp/max(int(prediction.sum()),1)))
    csv(out/'results/class_metrics.csv',pd.DataFrame(classes))
    cm = confusion_matrix(final.true_label,final.predicted_label,labels=[0,2])
    csv(out/'results/confusion_matrix.csv',pd.DataFrame({'true_label':[0,2],'predicted_0_back':cm[:,0],'predicted_2_back':cm[:,1]}))
    # Independent reconciliation from the serialized user deliverables.
    reread = pd.read_csv(out/'results/window_predictions.csv')
    check('exported_rows_accuracy',float((reread.true_label == reread.predicted_label).mean()),all_score['accuracy'],checks,atol=1e-15)
    check('subject_count_reconciliation',int(folds.n_query.sum()),len(final),checks)
    check('confusion_count_reconciliation',int(cm.sum()),len(final),checks)
    checks.append(dict(check='support_query_disjoint_and_7_7_6_all_subjects',passed=True))
    dump(out/'verification.json',dict(passed=True,scope='36 recordings, 6 models, 36 replay blocks; preprocessing parity on first recording',checks=checks))
    report = [
        '# GSPM-Net 六名被试真实 EEG 测试报告', '',
        f'生成时间：{run["started_at"]}。数据为本机下载的 COG-BCI 六名被试，非合成示例。', '',
        f'**最终合并窗口准确率：{all_score["accuracy"]:.2%}（{all_score["n_correct"]}/{len(final)}）。** '
        f'被试平均准确率为 {summary["subject_mean_accuracy"]:.2%}，被试间样本标准差为 {summary["subject_sd_accuracy"]:.2%}（不是置信区间）。', '',
        f'最终合并宏平均 F1：{all_score["f1_macro"]:.4f}；未经时序处理的原型分类准确率：{summary["raw_pooled"]["accuracy"]:.2%}。', '',
        '## 数据与标签', '',
        f'- 共 {len(manifest)} 份连续记录，三个 session、0-back 与 2-back 各一份；有效刺激窗口 {len(windows)} 个。',
        f'- 支持窗口 {summary["support_windows"]} 个；测试窗口 {len(final)} 个；剔除窗口 {summary["excluded_windows"]} 个，详见 excluded_windows.csv。',
        f'- 原始 500 Hz，模型固定 62 通道；记录长度 {manifest.duration_sec.min():.3f}–{manifest.duration_sec.max():.3f} 秒。',
        '- zeroBACK.set 对应真实标签 0-back，twoBACK.set 对应 2-back。真实标签表示任务条件，不是被试答题是否正确，也不是模型推测。',
        '- 0-back 使用事件 6021/6022；2-back 使用 6221/6222。内部索引 0/1 在交付预测表中转换为 0/2。',
        '- 原始通道、文件尺寸、SET/FDT 配对、有限数值和刺激边界均检查；原始数据及权重未改写。', '',
        '## 处理与评估方法', '',
        '1. 按模型顺序提取 62 通道；MAD 稳健尺度阈值 8 倍检测坏通道，以正常通道逐点中位数替换。',
        '2. 50 Hz 陷波（Q=30）、四阶 1–40 Hz 带通、通道中位数 ±10 倍稳健尺度截幅、平均参考、重采样至 250 Hz。',
        '3. 原始事件的 1-based latency 减 1 后除以 500 得到秒，再映射到 250 Hz，截取刺激后 2 秒；不用原始采样点直接切重采样信号。',
        '4. 每窗去通道均值，Welch 使用 1 秒 Hann 窗和 50% 重叠；五频带为 [4,8)、[8,13)、[13,20)、[20,30)、[30,40) Hz。',
        '5. 采用配套代码 ln(P + 1e-8)，论文写为 1e-12；本次未更改配套权重的输入数值协议。保留原始 EEGLAB 幅值，不额外缩放单位。',
        '6. 每人每类共 20 个支持窗口，按 S1/S2/S3 分配 7/7/6，取各任务最早有效窗口。只用本 session 支持集计算均值、标准差与初始原型。',
        '7. 使用本人留出折的冻结 GSC；高斯原型方差收缩 0.35、温度 0.7、预测后 EMA 更新率 0.05、方差下限 1e-4。',
        '8. EMA、Markov、滞回参数来自原 fold_metrics.csv 的独立验证被试；验证数据未下载，本次未重新选择参数。每份任务记录重新初始化原型及时序状态。', '',
        '## 按被试结果', '',
        '| 被试 | 原验证被试 | 支持/测试窗 | 原型准确率 | EMA 准确率 | 最终准确率 | 最终宏 F1 |',
        '|---|---|---:|---:|---:|---:|---:|',
    ]
    for r in folds.itertuples():
        report.append(f'| {r.subject} | {r.validation_subject} | {r.n_support}/{r.n_query} | {r.raw_accuracy:.2%} | {r.ema_accuracy:.2%} | {r.accuracy:.2%} | {r.f1_macro:.4f} |')
    report += ['', '## 混淆矩阵', '', '行是真实类别，列是最终预测类别；只含查询测试窗。', '',
               '| 真实类别 | 预测 0-back | 预测 2-back |', '|---|---:|---:|',
               f'| 0-back | {cm[0,0]} | {cm[0,1]} |', f'| 2-back | {cm[1,0]} | {cm[1,1]} |', '',
               '## 按 session 的最终结果', '', '| 被试 | session | 测试窗 | 准确率 | 宏 F1 |', '|---|---|---:|---:|---:|']
    for r in sessions:
        report.append(f'| {r["subject"]} | {r["session"]} | {r["n_query"]} | {r["accuracy"]:.2%} | {r["f1_macro"]:.4f} |')
    report += ['', '## 分类别结果', '', '| 被试 | 真实类别 | 窗口数 | 正确数 | 召回率 | 精确率 |', '|---|---|---:|---:|---:|---:|']
    for r in classes:
        report.append(f'| {r["subject"]} | {r["true_label"]}-back | {r["n_true"]} | {r["n_correct"]} | {r["recall"]:.2%} | {r["precision"]:.2%} |')
    report += ['', '## 数据质量与验收', '',
               f'- {int((manifest.bad_channels!="").sum())} 份记录进行了坏通道替换，共 {sum(len(s.split(";")) for s in manifest.bad_channels if s)} 个“记录×通道”；详情见 input_manifest.csv。',
               f'- {len(checks)} 项数值/隔离检查通过；详见 verification.json。',
               '- 首份真实记录的预处理与现有后端实现逐值对照；全部 36 份记录的 Welch 特征对照。',
               '- 六个真实权重分别检查 GSC 输出一致；36 个任务记录检查原型概率与时序实现一致。',
               '- 固定支持集与查询集后翻转全部查询标签，预测与更新诊断保持不变；改变后续特征，前 17 个窗口概率及判决保持不变。',
               '- 36 个任务记录均重复推理并核对概率完全相同；支持集标准化不使用查询统计，支持/查询窗口编号及时间区间不重叠。',
               '- 从导出的 CSV 独立重算准确率，核对混淆矩阵、各被试与总窗口数量。', '',
               '## 结果边界', '',
               '- 本次是六名已下载被试的离线少样本测试，不是完整 26 折复现，不能与论文 87.72% ±11.03% 直接等同。',
               '- 六名被试不是本次随机抽样；窗口属于重复测量，不把数千窗口当作独立被试推断人群表现。',
               '- 两类支持样本从不同任务记录事先收集；信号采用双向滤波。窗口递推因果检查只针对已经提取好的特征，不意味着原始信号处理是实时因果滤波。',
               '- 每份任务记录只有一个负荷条件；时序平滑在此协议下的表现不能直接代表真实场景中的负荷切换响应。',
               '- prob_0/prob_2 是模型概率，state_prob 是时序后验，均不是临床置信度。滞回判决可能与当前后验的最大值类别不同。',
               '- subject_metrics.csv 保留原实验对应六行历史成绩及差值，仅作对照，不根据差值调参。', '',
               '## 文件与复现', '',
               '- preprocessed/：连续预处理 EEG（data 形状为 通道×采样点，250 Hz），不要作为原始 EEG 再重复预处理。',
               '- features/all_windows.npz：5×62 特征缓存；window_manifest.csv、support_windows.csv、query_windows.csv 提供标签与划分。',
               '- results/window_predictions.csv：逐窗真实标签、原型与最终预测、概率、来源和模型版本；results/folds/ 保存各 session 的初始校准状态。',
               '- results/：总表、被试/session/类别指标与混淆矩阵；input_manifest.csv、model_audit.json、run_config.json 提供来源与哈希。',
               '- 运行方法和字段说明见 README.md。所有特征和结果均在本机产生，未上传。', '']
    (out/'测试报告.md').write_text('\n'.join(report),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=RESEARCH/'data')
    parser.add_argument('--out-dir',type=Path,default=ROOT/'提交文档/真实数据测试')
    parser.add_argument('--reuse-preprocessed',action='store_true',help='Reuse only hash-verified continuous preprocessing; regenerate features and all predictions/checks.')
    args = parser.parse_args()
    out, data_root = args.out_dir.resolve(), args.data_root.resolve()
    if out.is_relative_to(RESEARCH) or RESEARCH.is_relative_to(out):
        raise ValueError('Output must be separate from original research/data directory')
    for folder in ['preprocessed','events','features','results']:
        (out/folder).mkdir(parents=True,exist_ok=True)
    started = time.time()
    checks = []
    source_files = [Path(raw.__file__),Path(psd.__file__),Path(evaluation.__file__),
                    OLD/'ssl_common.py',OLD/'ssl_gated_source_pretrain.py',OLD/'nested_markov_ssl.py',
                    Path(app_signal.__file__),Path(app_engine.__file__),Path(app_network.__file__)]
    algorithms = {str(p):sha(p) for p in source_files}
    run = dict(started_at=datetime.now().astimezone().isoformat(),data_root=str(data_root),out_dir=str(out),
               subjects=SUBJECTS,pairing=PAIRING,preprocessing=PRE_OPTIONS,welch=PSD_OPTIONS,log_epsilon=1e-8,
               paper_log_epsilon=1e-12,seed=2026,cpu_threads=4,python=sys.version,
               versions={name:getattr(mod,'__version__') for name,mod in [('numpy',np),('scipy',scipy),('pandas',pd),('torch',torch),('sklearn',sklearn)]},
               platform=platform.platform(),source_hashes=algorithms,entrypoint_sha256=sha(__file__),
               original_eval_config_sha256=sha(EXPERIMENT/'13_Nback_24epoch/config.json'),
               original_fold_metrics_sha256=sha(EXPERIMENT/'13_Nback_24epoch/fold_metrics.csv'),
               status='running')
    dump(out/'run_config.json',run)
    try:
        channels = json.loads((CODE/'backend/models/folds/sub-01/manifest.json').read_text(encoding='utf-8'))['channels']
        if channels != app_signal.CHANNELS:
            raise AssertionError('Channel order mismatch')
        records = discover(data_root)
        data, windows, manifest = preprocess_all(records,channels,out,args.reuse_preprocessed,checks,algorithms)
        final, folds, windows = evaluate_all(data,windows,out,channels,checks)
        for path,digest in algorithms.items():
            if sha(path) != digest:
                raise AssertionError(f'Source modified during execution: {path}')
        write_report(out,final,folds,windows,manifest,checks,run)
        run.update(status='completed',elapsed_seconds=round(time.time()-started,3))
        dump(out/'run_config.json',run)
    except Exception as exc:
        run.update(status='failed',error=f'{type(exc).__name__}: {exc}',elapsed_seconds=round(time.time()-started,3))
        dump(out/'run_config.json',run)
        dump(out/'verification.json',dict(passed=False,error=run['error'],checks=checks))
        raise


if __name__ == '__main__':
    main()
