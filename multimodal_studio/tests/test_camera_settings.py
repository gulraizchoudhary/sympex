import unittest
from unittest.mock import Mock
from devices.camera_controls import normalize_settings, apply_camera_settings

class TestCameraSettings(unittest.TestCase):
    def test_normalization_and_validation(self):
        cfg=normalize_settings({'width':1280,'height':720,'fps':30,'autofocus':False})
        self.assertEqual((cfg['width'],cfg['height'],cfg['fps']),(1280,720,30.0))
        with self.assertRaises(ValueError):
            normalize_settings({'width': 0})

    def test_camera_properties_best_effort(self):
        class CV:
            CAP_PROP_FRAME_WIDTH=1; CAP_PROP_FRAME_HEIGHT=2; CAP_PROP_FPS=3
            CAP_PROP_AUTOFOCUS=4; CAP_PROP_FOCUS=5; CAP_PROP_AUTO_EXPOSURE=6
            CAP_PROP_EXPOSURE=7; CAP_PROP_BRIGHTNESS=8; CAP_PROP_GAIN=9
        cap=Mock()
        cap.set.return_value=False
        cap.get.return_value=0
        messages=[]
        result=apply_camera_settings(cap,CV,{'autofocus':False,'autoexposure':False},report=messages.append)
        self.assertIn('focus',result)
        self.assertIn('exposure',result)
        self.assertTrue(messages)
