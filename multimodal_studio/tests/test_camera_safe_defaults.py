import unittest
from unittest.mock import Mock
from devices.camera_controls import normalize_settings, apply_camera_settings

class CV:
    CAP_PROP_FRAME_WIDTH = 1
    CAP_PROP_FRAME_HEIGHT = 2
    CAP_PROP_FPS = 3
    CAP_PROP_AUTOFOCUS = 4
    CAP_PROP_FOCUS = 5
    CAP_PROP_AUTO_EXPOSURE = 6
    CAP_PROP_EXPOSURE = 7
    CAP_PROP_BRIGHTNESS = 8
    CAP_PROP_GAIN = 9
    CAP_PROP_AUTO_WB = 10
    CAP_PROP_BACKEND = 11
    CAP_DSHOW = 700
    CAP_MSMF = 1400

class SafeDefaults(unittest.TestCase):
    def test_safe_defaults_do_not_set_gain_brightness_manual_exposure(self):
        cap=Mock()
        cap.get.return_value = CV.CAP_DSHOW
        result=apply_camera_settings(cap, CV)
        self.assertNotIn('brightness', result)
        self.assertNotIn('gain', result)
        self.assertNotIn('exposure', result)
        self.assertIn('autoexposure', result)
        self.assertIn('auto_white_balance', result)
    def test_blank_manual_entries_are_ignored(self):
        d=normalize_settings({'brightness':'','gain':'','focus':'', 'exposure':''})
        self.assertIsNone(d['gain'])
        self.assertIsNone(d['focus'])
    def test_manual_value_requires_opt_in(self):
        cap=Mock()
        cap.get.return_value=CV.CAP_DSHOW
        out=apply_camera_settings(cap,CV,{'autoexposure':False,'exposure':-6,'gain':12})
        self.assertIn('exposure',out)
        self.assertIn('gain',out)
    def test_auto_exposure_magic_value_not_assumed_on_msmf(self):
        cap=Mock()
        cap.get.return_value=CV.CAP_MSMF
        out=apply_camera_settings(cap,CV)
        self.assertNotIn('autoexposure',out)

if __name__=='__main__':
    unittest.main()
