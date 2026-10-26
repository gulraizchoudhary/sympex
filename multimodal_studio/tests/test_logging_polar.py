import tempfile
import unittest
from pathlib import Path
from core import EventBus

class EventLoggingTests(unittest.TestCase):
    def test_preview_event_persisted_without_session(self):
        with tempfile.TemporaryDirectory() as td:
            bus = EventBus(td)
            bus.emit('Polar H10', 'ERROR', "Preview start failed: ValueError('Scan/select a Polar H10 first')")
            bus.emit('Polar H10', 'DROPPED', 'No BLE packets for six seconds')
            bus.emit('Microphone', 'WARNING', 'Silent input')
            self.assertEqual(bus.events.qsize(), 3)
            content = (Path(td) / 'application.log').read_text(encoding='utf8')
            for string in ('Preview start failed', 'DROPPED', 'WARNING', 'No BLE packets'):
                self.assertIn(string, content)
            bus.close()

    def test_session_events_in_both_logs_and_close(self):
        with tempfile.TemporaryDirectory() as td:
            bus = EventBus(Path(td) / 'application')
            session = Path(td) / 'session'
            session.mkdir()
            bus.open_session(session)
            bus.emit('SeeTrue', 'CAUTION', 'Missing samples')
            bus.close_session()
            bus.emit('Polar H10', 'ERROR', 'Preview failed after session closed')
            self.assertIn('Missing samples', (session / 'events.log').read_text())
            self.assertNotIn('Preview failed', (session / 'events.log').read_text())
            self.assertIn('Preview failed', (Path(td) / 'application' / 'application.log').read_text())
            bus.close()

if __name__ == '__main__':
    unittest.main()
