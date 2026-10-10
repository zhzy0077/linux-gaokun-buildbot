"""Exercise the Gaokun-only primary-plane mask without building a kernel."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'patches/others/0011-drm-msm-dpu-pin-Gaokun3-primary-planes.patch'


def added_source():
    return '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                     if line.startswith('+') and not line.startswith('+++'))


def mask_function():
    return re.search(r'static unsigned long dpu_plane_possible_crtcs\(.*?\n}',
                     added_source(), re.S).group(0)


class DpuPlaneOwnershipTests(unittest.TestCase):
    def test_both_plane_backends_use_the_primary_index(self):
        source = added_source()
        self.assertIn('#include <linux/of.h>', source)
        self.assertRegex(source, r'dpu_plane_possible_crtcs\(type, primary_planes_idx,\s+max_crtc_count\)')
        self.assertIn('dpu_plane_init_virtual(dev, type, possible_crtcs)', source)
        patched_context = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                                    if line.startswith((' ', '+')) and not line.startswith('+++'))
        self.assertRegex(patched_context, r'dpu_plane_init\(dev, catalog->sspp\[i\].id, type,\s+possible_crtcs\)')
        self.assertNotIn('encoder->possible_crtcs', source)
        self.assertNotIn('dpu_use_virtual_planes =', source)
        self.assertEqual(PATCH.read_text().count('diff --git '), 1)

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
    def test_board_scope_masks_and_cross_crtc_rejection(self):
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#define BIT(n) (1UL << (n))
enum drm_plane_type { DRM_PLANE_TYPE_PRIMARY, DRM_PLANE_TYPE_CURSOR,
                      DRM_PLANE_TYPE_OVERLAY };
static const char *machine;
static bool of_machine_is_compatible(const char *compatible)
{ return machine && !strcmp(machine, compatible); }
''' + mask_function() + r'''
int main(void)
{
    const char *boards[] = {"huawei,gaokun3", "qcom,sc8280xp",
                           "lenovo,thinkpad-x13s", "huawei,other", NULL};
    for (unsigned b = 0; b < sizeof(boards)/sizeof(boards[0]); b++) {
        machine = boards[b];
        for (unsigned count = 1; count <= 8; count++) {
            unsigned long all = BIT(count) - 1;
            unsigned long union_mask = 0;
            for (unsigned primary = 0; primary < count; primary++) {
                unsigned long mask = dpu_plane_possible_crtcs(
                    DRM_PLANE_TYPE_PRIMARY, primary, count);
                assert(mask == (b == 0 ? BIT(primary) : all));
                union_mask |= mask;
                if (b == 0) {
                    /* Any client enumeration order must keep plane i on CRTC i. */
                    for (unsigned crtc = 0; crtc < count; crtc++)
                        assert(!!(mask & BIT(crtc)) == (primary == crtc));
                }
            }
            assert(union_mask == all); /* Every CRTC remains usable. */
            /* Cursor/overlay SSPPs may occur between or after primary SSPPs.
             * Their masks must not depend on the current primary counter. */
            for (unsigned primary = 0; primary <= count; primary++) {
                assert(dpu_plane_possible_crtcs(DRM_PLANE_TYPE_CURSOR,
                                               primary, count) == all);
                assert(dpu_plane_possible_crtcs(DRM_PLANE_TYPE_OVERLAY,
                                               primary, count) == all);
            }
        }
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='gaokun-dpu-test-') as directory:
            path = Path(directory) / 'test.c'
            binary = Path(directory) / 'test'
            path.write_text(source)
            result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                     '-fsanitize=undefined', str(path), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
