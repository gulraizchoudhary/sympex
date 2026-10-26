import sys
import types
import unittest
from unittest.mock import MagicMock

# Validate selection without needing LSL hardware or its native library.
if 'pylsl' not in sys.modules:
    fake = types.ModuleType('pylsl')
    for name in ('StreamInfo', 'StreamOutlet', 'StreamInlet'):
        setattr(fake, name, MagicMock())
    fake.resolve_streams = MagicMock(return_value=[])
    fake.local_clock = lambda: 1.0
    sys.modules['pylsl'] = fake

from devices.seetrue import SeeTrueRecorder

class ChannelSelectionTests(unittest.TestCase):
    def test_sparse_selection_preserves_indices(self):
        self.assertEqual(SeeTrueRecorder.validate_selection([11, 1, 6], 20), [1, 6, 11])

    def test_all_channels(self):
        self.assertEqual(SeeTrueRecorder.validate_selection(list(range(20)), 20), list(range(20)))

    def test_empty_invalid(self):
        with self.assertRaises(ValueError):
            SeeTrueRecorder.validate_selection([], 15)

    def test_out_of_bounds_invalid(self):
        with self.assertRaises(ValueError):
            SeeTrueRecorder.validate_selection([1, 15], 15)

    def test_duplicate_invalid(self):
        with self.assertRaises(ValueError):
            SeeTrueRecorder.validate_selection([2, 2], 15)

if __name__ == '__main__':
    unittest.main()
