#!/usr/bin/python3
"""Exercise the installed Mutter's system monitor defaults in isolated sessions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

if '--child' in sys.argv:
    from gi.repository import Gio
    log = Path(os.environ['HOME']) / 'mutter.log'
    with log.open('w') as output:
        p = subprocess.Popen(['mutter', '--headless', '--no-x11', '--wayland-display=gaokun-monitor-test', '--virtual-monitor=1600x2560'], stdout=output, stderr=output)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            for _ in range(60):
                try:
                    state = bus.call_sync('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', None, None, Gio.DBusCallFlags.NONE, 500, None).unpack()
                    if state[2]:
                        print(json.dumps(state)); break
                except Exception:
                    pass
                if p.poll() is not None:
                    raise RuntimeError(log.read_text())
                time.sleep(0.15)
            else:
                raise RuntimeError('Mutter startup timed out: ' + log.read_text())
        finally:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
    sys.exit(0)


def probe(system_xml=None, user_xml=None):
    with tempfile.TemporaryDirectory(prefix='gaokun-monitor-test.') as d:
        root = Path(d)
        env = dict(os.environ)
        for variable, folder in {'HOME':'home', 'XDG_CONFIG_HOME':'user', 'XDG_CONFIG_DIRS':'system', 'XDG_DATA_HOME':'data', 'XDG_STATE_HOME':'state', 'XDG_CACHE_HOME':'cache', 'XDG_RUNTIME_DIR':'run'}.items():
            path = root / folder; path.mkdir(mode=0o700); env[variable] = str(path)
        for variable in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(variable, None)
        if system_xml: (root/'system/monitors.xml').write_bytes(system_xml)
        if user_xml: (root/'user/monitors.xml').write_bytes(user_xml)
        p = subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--child'], env=env, text=True, capture_output=True, timeout=25)
        if p.returncode: raise RuntimeError(p.stdout + p.stderr)
        state = json.loads(p.stdout.strip().splitlines()[-1])
        return {'state': state, 'log': (root/'home/mutter.log').read_text()}

baseline = probe()
spec = baseline['state'][1][0][0]
xml = ET.parse('/usr/share/gaokun3/monitors.xml')
for name, value in zip(('connector','vendor','product','serial'), spec):
    xml.find('.//monitorspec/' + name).text = value
right_xml = ET.tostring(xml.getroot())
right = probe(right_xml)
xml.find('.//rotation').text = 'left'
left = probe(right_xml, ET.tostring(xml.getroot()))
result = {'baseline': baseline, 'system_default': right, 'user_override': left}
assert baseline['state'][2][0][3] == 0, baseline
assert right['state'][2][0][3] == 3, right
assert left['state'][2][0][3] == 1, left
print(json.dumps(result, indent=2))
