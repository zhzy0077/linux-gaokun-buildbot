"""Exercise the candidate initializer with Mutter 50.5's real plane selector."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'packaging/mutter/0001-native-inherit-kernel-plane-assignments.patch'
FIXTURE = ROOT / 'tests/fixtures/mutter-50.5-plane-selection.c'


class MutterPlaneOwnershipTests(unittest.TestCase):
    def test_snapshot_is_used_to_seed_assignments(self):
        patch = PATCH.read_text()
        self.assertIn('+  plane->initial_crtc_id = drm_plane->crtc_id;', patch)
        self.assertIn('+  init_crtc_plane_assignments (crtc_kms);', patch)
        removed = '\n'.join(l for l in patch.splitlines()
                            if l.startswith('-') and not l.startswith('---'))
        self.assertEqual(removed, '')
        # Existing runtime selection/ownership rules must remain intact.
        self.assertNotIn('find_unassigned_plane', removed)
        self.assertNotIn('is_plane_active', removed)

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
    def test_inherited_and_runtime_plane_ownership(self):
        added = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        initializer = re.search(r'static void\ninit_crtc_plane_assignments\s*\(.*?\n}',
                                added, re.S).group(0)
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
typedef int gboolean;
#define TRUE 1
#define FALSE 0
#define meta_topic(...) ((void)0)
#define g_assert_not_reached() abort()
#define META_IS_CRTC_KMS(c) ((c) != NULL)
#define g_ptr_array_index(a,i) ((a)->data[i])
typedef enum { META_KMS_PLANE_TYPE_PRIMARY, META_KMS_PLANE_TYPE_CURSOR,
               META_KMS_PLANE_TYPE_OVERLAY } MetaKmsPlaneType;
typedef struct GList { void *data; struct GList *next; } GList;
typedef struct { size_t len; void **data; } GPtrArray;
typedef struct { uint32_t id, initial_crtc_id, possible_crtcs;
                 MetaKmsPlaneType type; } MetaKmsPlane;
typedef struct MetaKmsDevice { GList *planes, *crtcs; } MetaKmsDevice;
typedef struct MetaCrtcKms MetaCrtcKms;
typedef struct { uint32_t id, idx; MetaKmsDevice *device;
                 MetaCrtcKms *wrapper; } MetaKmsCrtc;
struct MetaCrtcKms { MetaKmsCrtc *kms_crtc;
                    MetaKmsPlane *assigned_primary_plane, *assigned_cursor_plane; };
typedef struct { MetaKmsPlane *primary_plane, *cursor_plane; } CrtcKmsAssignment;
typedef struct { MetaCrtcKms *crtc; void *backend_private; } MetaCrtcAssignment;
static uint32_t meta_kms_plane_get_initial_crtc_id(MetaKmsPlane *p) { return p->initial_crtc_id; }
static MetaKmsPlaneType meta_kms_plane_get_plane_type(MetaKmsPlane *p) { return p->type; }
static uint32_t meta_kms_crtc_get_id(MetaKmsCrtc *c) { return c->id; }
static MetaKmsDevice *meta_kms_crtc_get_device(MetaKmsCrtc *c) { return c->device; }
static MetaKmsCrtc *meta_crtc_kms_get_kms_crtc(MetaCrtcKms *c) { return c->kms_crtc; }
static MetaCrtcKms *meta_crtc_kms_from_kms_crtc(MetaKmsCrtc *c) { return c->wrapper; }
static GList *meta_kms_device_get_planes(MetaKmsDevice *d) { return d->planes; }
static GList *meta_kms_device_get_crtcs(MetaKmsDevice *d) { return d->crtcs; }
static gboolean meta_kms_plane_is_usable_with(MetaKmsPlane *p, MetaKmsCrtc *c)
{ return !!(p->possible_crtcs & (1u << c->idx)); }
'''
        harness += initializer + '\n' + FIXTURE.read_text()
        harness += r'''
int main(void)
{
    MetaKmsDevice dev = {0};
    MetaKmsCrtc crtc[2] = {{91, 0, &dev, NULL}, {92, 1, &dev, NULL}};
    MetaCrtcKms wrapper[2] = {{&crtc[0], NULL, NULL}, {&crtc[1], NULL, NULL}};
    GList crtcs[2] = {{&crtc[0], NULL}, {&crtc[1], NULL}};
    MetaKmsPlane plane[6] = {
        {43, 92, 3, META_KMS_PLANE_TYPE_PRIMARY},
        {49, 91, 3, META_KMS_PLANE_TYPE_PRIMARY},
        {85, 92, 3, META_KMS_PLANE_TYPE_CURSOR},
        {55, 91, 3, META_KMS_PLANE_TYPE_OVERLAY},
        {61, 0,  3, META_KMS_PLANE_TYPE_PRIMARY},
        {67, 999, 3, META_KMS_PLANE_TYPE_CURSOR},
    };
    GList planes[6];
    for (int i = 0; i < 6; i++) {
        planes[i].data = &plane[i]; planes[i].next = i < 5 ? &planes[i+1] : NULL;
    }
    crtcs[0].next = &crtcs[1]; dev.crtcs = crtcs; dev.planes = planes;
    crtc[0].wrapper = &wrapper[0]; crtc[1].wrapper = &wrapper[1];
    GPtrArray assignments = {0};

    /* Unpatched startup picks plane 43 for CRTC 91 despite kernel owner 92. */
    MetaKmsPlane *p = find_unassigned_plane(&wrapper[0], META_KMS_PLANE_TYPE_PRIMARY, &assignments);
    assert(p == &plane[0] && p->initial_crtc_id != crtc[0].id);

    init_crtc_plane_assignments(&wrapper[0]);
    init_crtc_plane_assignments(&wrapper[1]);
    assert(wrapper[0].assigned_primary_plane == &plane[1]);
    assert(wrapper[1].assigned_primary_plane == &plane[0]);
    assert(wrapper[0].assigned_cursor_plane == NULL);
    assert(wrapper[1].assigned_cursor_plane == &plane[2]);
    assert(find_unassigned_plane(&wrapper[0], META_KMS_PLANE_TYPE_PRIMARY, &assignments) == &plane[1]);
    assert(find_unassigned_plane(&wrapper[1], META_KMS_PLANE_TYPE_PRIMARY, &assignments) == &plane[0]);

    /* Subsequent ownership comes from live compositor assignments, not the
     * startup snapshot. A formerly unbound plane active elsewhere stays reserved. */
    wrapper[0].assigned_primary_plane = &plane[4];
    wrapper[1].assigned_primary_plane = NULL;
    assert(is_plane_active(&dev, &plane[4]));
    assert(!is_plane_active(&dev, &plane[1]));
    dev.planes = &planes[4]; planes[4].next = &planes[1]; planes[1].next = NULL;
    assert(find_unassigned_plane(&wrapper[1], META_KMS_PLANE_TYPE_PRIMARY, &assignments) == &plane[1]);

    /* Also exclude a free plane already selected by this configuration. */
    CrtcKmsAssignment chosen = {&plane[1], NULL};
    MetaCrtcAssignment assignment = {&wrapper[0], &chosen};
    void *items[] = {&assignment}; assignments = (GPtrArray){1, items};
    assert(find_unassigned_plane(&wrapper[1], META_KMS_PLANE_TYPE_PRIMARY, &assignments) == NULL);

    /* Reject unusable inherited bindings and ignore unbound/overlay planes. */
    wrapper[0].assigned_primary_plane = NULL; wrapper[0].assigned_cursor_plane = NULL;
    plane[1].possible_crtcs = 2;
    init_crtc_plane_assignments(&wrapper[0]);
    assert(wrapper[0].assigned_primary_plane == NULL);
    assert(wrapper[0].assigned_cursor_plane == NULL);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='gaokun-mutter-test-') as directory:
            source = Path(directory) / 'test.c'
            binary = Path(directory) / 'test'
            source.write_text(harness)
            result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                     '-fsanitize=undefined', str(source), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
