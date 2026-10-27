import unittest
from devices.windows_audio import safe_hardware_match

class TestHardwareMatching(unittest.TestCase):
    def test_realtek_safe(self):
        self.assertEqual(safe_hardware_match('Microphone Array (Realtek(R) Audio)', ['Realtek(R) Audio']), 'Realtek(R) Audio')
    def test_generic_not_misrepresented(self):
        self.assertIsNone(safe_hardware_match('Microphone (USB Audio Device)', ['Ultramic384K PRO', 'Realtek(R) Audio']))
    def test_ambiguous_not_misrepresented(self):
        self.assertIsNone(safe_hardware_match('Array (Realtek Audio)', ['Realtek Audio', 'Realtek-Audio']))

if __name__=='__main__': unittest.main()
