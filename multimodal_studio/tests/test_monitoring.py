import unittest
from monitoring import aged_points

class RollingAxisTests(unittest.TestCase):
    def test_window_spans_entire_5_seconds(self):
        pts=aged_points([(95, 1), (96, 2), (97, 3), (98, 4), (99, 5), (100, 6)],100,5)
        self.assertEqual([x for x,_ in pts],[5,4,3,2,1,0])
    def test_outside_window_excluded(self):
        self.assertEqual(aged_points([(94,1),(95,2),(100,3),(101,4)],100,5),[(5,2),(0,3)])
    def test_no_fake_fill_for_partial_history(self):
        self.assertEqual(aged_points([(98,1),(100,2)],100,5),[(2,1),(0,2)])
    def test_bad_window_rejected(self):
        with self.assertRaises(ValueError): aged_points([],100,0)

if __name__=='__main__': unittest.main()
