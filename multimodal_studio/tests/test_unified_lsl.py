import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class UnifiedLSLTests(unittest.TestCase):
    def test_single_incoming_entry_point(self):
        app = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertNotIn("self.see_tab", app)
        self.assertNotIn("self.enabled['SeeTrue']", app)
        self.assertIn('NetworkLSLRecorder.scan', app)
        self.assertIn('NetworkLSLRecorder.validate_selection', app)

    def test_outgoing_names(self):
        polar = (ROOT / 'devices' / 'polar.py').read_text(encoding='utf-8')
        core = (ROOT / 'core.py').read_text(encoding='utf-8')
        self.assertIn("f'IMS_PolarH10_{name.upper()}'", polar)
        self.assertIn('IMS_Experiment_Markers', core)
        self.assertIn('self.lsl_instance_id', polar)
        self.assertIn('uuid.uuid4().hex', core)

if __name__ == '__main__':
    unittest.main()
