"""Boundary checks for the new dataset adapter (the research code is untouched)."""
from pathlib import Path
import argparse
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import evaluate_downloaded_cog as pipeline


def event(code, latency):
    return SimpleNamespace(type=code, latency=latency)


class DatasetAdapterChecks(unittest.TestCase):
    def record(self, path=Path('zeroBACK.set')):
        return pipeline.raw.Recording('sub-01', 'ses-S1', '0-back', 0, path)

    def test_original_latency_maps_to_resampled_seconds(self):
        meta = dict(sfreq=500., events=[event('6021',501),event('6031',1001)])
        rows, excluded = pipeline.event_windows(self.record(),meta,2000)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['original_onset_sec'],1.)
        self.assertEqual(rows[0]['start_sample_250hz'],250)
        self.assertFalse(excluded)

    def test_last_full_window_valid_short_tail_rejected(self):
        meta = dict(sfreq=500., events=[event('6021',1001),event('6022',1501)])
        rows, excluded = pipeline.event_windows(self.record(),meta,1000)
        self.assertEqual([r['start_sample_250hz'] for r in rows],[500])
        self.assertEqual(excluded[0]['reason'],'out_of_bounds')

    def test_internal_boundary_excludes_crossing_window(self):
        meta = dict(sfreq=500., events=[event('boundary',751),event('6021',501),event('6022',2001)])
        rows, excluded = pipeline.event_windows(self.record(),meta,2000)
        self.assertEqual(len(rows),1)
        self.assertEqual(excluded[0]['reason'],'crosses_boundary')

    def test_duplicate_events_rejected(self):
        meta = dict(sfreq=500., events=[event('6021',501),event('6022',501)])
        with self.assertRaisesRegex(ValueError,'duplicate'):
            pipeline.event_windows(self.record(),meta,2000)

    def test_nonfinite_event_rejected(self):
        with self.assertRaisesRegex(ValueError,'Nonfinite'):
            pipeline.event_windows(self.record(),dict(sfreq=500.,events=[event('6021',np.nan)]),2000)

    def test_nested_subjects_and_missing_record(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for subject in pipeline.SUBJECTS:
                parent = root/subject if subject == 'sub-01' else root/subject/subject
                for session in ['ses-S1','ses-S2','ses-S3']:
                    eeg = parent/session/'eeg'
                    eeg.mkdir(parents=True)
                    for filename,_ in pipeline.raw.TASK_FILES.values():
                        (eeg/filename).touch()
            records = pipeline.discover(root)
            self.assertEqual(len(records),36)
            records[-1].set_path.unlink()
            with self.assertRaisesRegex(ValueError,'incomplete'):
                pipeline.discover(root)

    def test_missing_fdt_duplicate_channels_bad_fs_nan_and_short_record(self):
        channels = pipeline.app_signal.CHANNELS
        with TemporaryDirectory() as folder:
            root = Path(folder)
            fdt = root/'sample.fdt'
            meta = dict(fdt_path=fdt,nbchan=62,pnts=2000,trials=1,sfreq=500.,ch_names=channels.copy())
            record = self.record(root/'sample.set')
            with patch.object(pipeline.raw,'load_set_metadata',return_value=meta):
                with self.assertRaisesRegex(ValueError,'FDT'):
                    pipeline.validate_record(record,channels)
            fdt.touch()
            cases = [dict(meta,ch_names=channels[:-1]+[channels[0]]),
                     dict(meta,ch_names=channels[:-1]+['Unknown']),dict(meta,sfreq=0.),dict(meta,pnts=500)]
            for bad_meta in cases:
                with patch.object(pipeline.raw,'load_set_metadata',return_value=bad_meta):
                    with self.assertRaises(ValueError):
                        pipeline.validate_record(record,channels)
            with patch.object(pipeline.raw,'load_set_metadata',return_value=meta):
                with self.assertRaisesRegex(ValueError,'size mismatch'):
                    pipeline.validate_record(record,channels)
            with patch.object(pipeline.raw,'load_set_metadata',return_value=meta), patch.object(pipeline.raw,'read_fdt',return_value=np.full((62,2000),np.nan)):
                with self.assertRaisesRegex(ValueError,'NaN/Inf'):
                    pipeline.validate_record(record,channels)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json-output',type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DatasetAdapterChecks))
    if args.json_output:
        pipeline.dump(args.json_output,dict(passed=result.wasSuccessful(),tests_run=result.testsRun,
                                            failures=len(result.failures),errors=len(result.errors),
                                            cases=unittest.defaultTestLoader.getTestCaseNames(DatasetAdapterChecks)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
