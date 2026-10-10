"""Gaokun UCSI regression tests; execute the driver's worker/state logic in C."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'drivers/gaokun-ec/ucsi_huawei_gaokun.c'


def function(source, name):
    return re.search(r'static (?:int|void) ' + name + r'\(.*?\n\}',
                     source, re.S).group(0)


class GaokunUcsiTests(unittest.TestCase):
    def test_irq_never_waits_for_usb_or_ppm(self):
        source = SOURCE.read_text()
        notify = function(source, 'gaokun_ucsi_notify')
        for forbidden in ('wait_for_completion', 'mutex_lock', 'typec_mux_set',
                          'drm_aux_hpd_bridge_notify'):
            self.assertNotIn(forbidden, notify)
        self.assertIn('queue_delayed_work', notify)
        self.assertNotIn('wait_for_completion_timeout', source)

    def test_early_probe_does_not_access_ec(self):
        source = SOURCE.read_text()
        ports = function(source, 'gaokun_ucsi_ports_init')
        self.assertNotIn('gaokun_ec_ucsi_get_reg', ports)
        self.assertIn('uec->num_ports = num_ports', ports)
        self.assertIn('num_ports > GAOKUN_UCSI_MAX_PORTS', ports)
        self.assertIn('devm_add_action_or_reset', ports)

    def test_removal_stops_producers_before_consumers(self):
        body = function(SOURCE.read_text(), 'gaokun_ucsi_remove')
        operations = ['disable_delayed_work_sync(&uec->work)',
                      'gaokun_ec_unregister_notify',
                      'disable_delayed_work_sync(&uec->sync_work)',
                      'ucsi_unregister', 'ucsi_destroy']
        positions = [body.index(op) for op in operations]
        self.assertEqual(positions, sorted(positions))

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
    def test_state_replay_retry_and_hotplug(self):
        source = SOURCE.read_text()
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define HZ 100
#define BIT(i) (1U << (i))
#define USB_SID_DISPLAYPORT 0xff01
#define GAOKUN_UCSI_NO_PORT_UPDATE (-1)
#define GAOKUN_UCSI_REGISTER_DELAY (3 * HZ)
#define GAOKUN_UCSI_RETRY_DELAY (10 * HZ)
#define GAOKUN_UCSI_MAX_RETRIES 3
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define to_delayed_work(p) container_of(p, struct delayed_work, work)
#define smp_load_acquire(p) (*(p))
#define smp_store_release(p,v) (*(p) = (v))
#define dev_err(...) ((void)0)
#define dev_warn(...) ((void)0)
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; bool pending; };
struct bridge { int dev; };
struct alt { u16 svid; };
struct mux_state { int mode; struct alt *alt; };
struct gaokun_ucsi_port {
    struct gaokun_ucsi *ucsi;
    struct bridge *bridge;
    int idx, ccx, mode, svid, hpd_state, hpd_irq;
    int applied_ccx, applied_svid, applied_mode;
    bool applied, applied_hpd;
    struct alt dp_alt;
    struct mux_state state;
    void *typec_mux;
};
struct gaokun_ucsi_reg { u8 num_ports, port_updt, port_data[4], checksum, reserved; };
struct ucsi { struct delayed_work work; int ppm_lock; void *connector;
              struct { int num_connectors; } cap; };
struct gaokun_ucsi {
    struct ucsi *ucsi;
    struct delayed_work work, sync_work;
    int ec, nb, dev;
    u8 num_ports, retries;
    bool ready, registered, notifier_registered;
    struct gaokun_ucsi_port ports[2];
};
enum { connector_status_connected = 1, connector_status_disconnected = 2 };
static void *system_wq;
static struct gaokun_ucsi_reg snapshot;
static int orientations[2], mux_calls, hpd_calls, last_hpd;
static int read_error, mux_error, ack_mask, ack_error, empty_acks;
static int core_outcome, registrations, unregistrations, notifier_calls;
static int dummy_connectors[2];
static struct ucsi *active_core;
static void mutex_lock(int *m) { assert(!*m); *m = 1; }
static void mutex_unlock(int *m) { assert(*m); *m = 0; }
static void gaokun_set_orientation(void *con, struct gaokun_ucsi_port *p)
{ (void)con; orientations[p->idx] = p->ccx; }
static int typec_mux_set(void *mux, struct mux_state *state)
{ (void)mux; (void)state; ++mux_calls; return mux_error; }
static void drm_aux_hpd_bridge_notify(int *dev, int status)
{ (void)dev; ++hpd_calls; last_hpd = status; }
static int gaokun_ec_ucsi_get_reg(int ec, struct gaokun_ucsi_reg *reg)
{ (void)ec; *reg = snapshot; return read_error; }
static int gaokun_ec_ucsi_pan_ack(int ec, int port)
{ (void)ec; if (port < 0) ++empty_acks; else ack_mask |= BIT(port); return ack_error; }
/* Decode only the fields consumed by the real apply/sync functions. */
static void gaokun_ucsi_port_update(struct gaokun_ucsi_port *p, const u8 *data)
{
    p->ccx = data[2*p->idx] & 3;
    p->svid = (data[2*p->idx] & 8) ? USB_SID_DISPLAYPORT : 0;
    p->mode = data[2*p->idx+1] & 15;
    p->hpd_state = !!(data[2*p->idx+1] & 16);
    p->hpd_irq = !!(data[2*p->idx+1] & 32);
}
static void queue_delayed_work(void *wq, struct delayed_work *w, int delay)
{ (void)wq; (void)delay; w->pending = true; }
static void mod_delayed_work(void *wq, struct delayed_work *w, int delay)
{ queue_delayed_work(wq, w, delay); }
static void schedule_delayed_work(struct delayed_work *w, int delay)
{ queue_delayed_work(NULL, w, delay); }
static bool delayed_work_pending(struct delayed_work *w) { return w->pending; }
static int gaokun_ec_register_notify(int ec, int *nb)
{ (void)ec; (void)nb; ++notifier_calls; return 0; }
static int ucsi_register(struct ucsi *u)
{ ++registrations; active_core = u; u->work.pending = true; return 0; }
static void ucsi_unregister(struct ucsi *u)
{ assert(!u->connector); ++unregistrations; u->work.pending = false; }
static void flush_delayed_work(struct delayed_work *w)
{
    assert(w == &active_core->work);
    w->pending = core_outcome == 2; /* role-switch probe deferral */
    if (core_outcome == 1) {
        active_core->connector = dummy_connectors;
        active_core->cap.num_connectors = 2;
    }
}
'''
        # Use an int pointer for connector[] in the native harness.
        harness = harness.replace('void *connector;', 'int *connector;')
        for name in ('gaokun_ucsi_apply_port', 'gaokun_ucsi_sync_worker',
                     'gaokun_ucsi_register_worker'):
            harness += '\n' + function(source, name)
        harness += r'''
static void sync(struct gaokun_ucsi *u)
{ u->sync_work.pending = false; ack_mask = 0; gaokun_ucsi_sync_worker(&u->sync_work.work); }
int main(void)
{
    struct ucsi core = {0};
    struct gaokun_ucsi u = {.ucsi = &core, .num_ports = 2};
    struct bridge bridges[2] = {{0}, {1}};
    for (int i = 0; i < 2; ++i) {
        u.ports[i].idx = i; u.ports[i].ucsi = &u; u.ports[i].bridge = &bridges[i];
    }
    /* Hardware reproduction: port 0 is DP+HPD; only port 1 has an update. */
    snapshot = (struct gaokun_ucsi_reg){2, 2, {8, 0x13, 4, 0}, 0x79, 0};
    sync(&u);
    assert(ack_mask == 2 && mux_calls == 0 && hpd_calls == 0);
    assert(!u.ready); /* Never access connector[] before async init finishes. */

    core_outcome = 0; /* First UCSI initialization fails asynchronously. */
    gaokun_ucsi_register_worker(&u.work.work);
    assert(registrations == 1 && unregistrations == 1 && !u.ready);
    assert(u.work.pending && u.retries == 1 && notifier_calls == 1);
    assert(u.ports[0].bridge == &bridges[0]); /* Preserve the DRM graph. */
    core_outcome = 2;
    gaokun_ucsi_register_worker(&u.work.work);
    assert(!u.ready && u.registered && registrations == 2 && unregistrations == 1);
    core_outcome = 1;
    gaokun_ucsi_register_worker(&u.work.work);
    assert(u.ready && u.sync_work.pending && registrations == 2 && notifier_calls == 1);
    sync(&u);
    assert(u.ports[0].applied_hpd && u.ports[0].applied_svid == USB_SID_DISPLAYPORT);
    assert(orientations[0] == 0 && mux_calls == 2 && ack_mask == 2);

    int calls = hpd_calls;
    snapshot.port_updt = 0;
    sync(&u);
    assert(hpd_calls == calls && empty_acks == 1); /* No duplicate HPD storm. */
    snapshot.port_data[1] |= 32;
    sync(&u);
    assert(hpd_calls == calls); /* Stale IRQ bit without a port update. */
    snapshot.port_updt = 1;
    sync(&u);
    assert(hpd_calls == calls + 1 && last_hpd == connector_status_connected);
    snapshot.port_data[1] &= ~32;

    /* Disconnect is propagated even when the new SVID is no longer DP. */
    snapshot.port_data[0] = 2; snapshot.port_data[1] = 0; snapshot.port_updt = 3;
    sync(&u);
    assert(!u.ports[0].applied_hpd && last_hpd == connector_status_disconnected);
    assert(ack_mask == 3);

    /* Reversed reconnect and failed mux must be retried before ACK. */
    snapshot.port_data[0] = 9; snapshot.port_data[1] = 0x13; snapshot.port_updt = 1;
    mux_error = -EIO;
    sync(&u);
    assert(orientations[0] == 1 && !(ack_mask & 1) && u.sync_work.pending);
    mux_error = 0;
    sync(&u);
    assert(ack_mask == 1 && u.ports[0].applied_hpd);
    read_error = -EIO;
    sync(&u);
    assert(ack_mask == 0 && u.sync_work.pending);
    read_error = 0; snapshot.num_ports = 3;
    sync(&u);
    assert(ack_mask == 0 && u.sync_work.pending); /* Bounds before port access. */
    snapshot.num_ports = 2; ack_error = -EIO;
    sync(&u);
    assert(u.sync_work.pending);

    /* Retry exhaustion is bounded and never touches bridge objects. */
    struct ucsi failed_core = {0};
    struct gaokun_ucsi failed = {.ucsi = &failed_core, .num_ports = 2};
    core_outcome = 0;
    for (int i = 0; i < 4; ++i) {
        failed.work.pending = false;
        gaokun_ucsi_register_worker(&failed.work.work);
        assert(failed.work.pending == (i < 3));
    }
    assert(!failed.ready && !failed.registered);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='gaokun-ucsi-test-') as directory:
            path = Path(directory) / 'test.c'
            binary = Path(directory) / 'test'
            path.write_text(harness)
            result = subprocess.run(
                ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=undefined', str(path), '-o', str(binary)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
