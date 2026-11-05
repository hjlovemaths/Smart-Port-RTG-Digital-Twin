"""Capture row padding, colour order and callback-buffer ownership."""
import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
from role_viewport import copy_bgra_frame


class FrameCopyTests(unittest.TestCase):
    def test_rgba_padded_rows_become_owned_contiguous_bgra(self):
        source=np.arange(24,dtype=np.uint8)
        result=copy_bgra_frame(source,2,2,rgba=True)
        expected=source.reshape(2,12)[:,:8].reshape(2,2,4)[:,:,[2,1,0,3]]
        np.testing.assert_array_equal(result,expected)
        self.assertTrue(result.flags.c_contiguous)
        self.assertFalse(np.shares_memory(result,source))
        original=result.copy();source[:]=0
        np.testing.assert_array_equal(result,original)

    def test_bgra_preserves_channels_without_padding(self):
        source=np.arange(16,dtype=np.uint8)
        result=copy_bgra_frame(source,2,2)
        np.testing.assert_array_equal(result,source.reshape(2,2,4))
        self.assertTrue(result.flags.c_contiguous)
        self.assertFalse(np.shares_memory(result,source))


if __name__=='__main__':unittest.main()
