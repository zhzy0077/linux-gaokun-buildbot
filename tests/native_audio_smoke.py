#!/usr/bin/env python3
"""Check UCM selection in both audio daemons and play two seconds of silence.

Run as the logged-in desktop user. Does not change volume or capture audio.
A running hardware PCM is verified; audible output is a separate human test.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time

EXPECTED_UCM = '/usr/share/gaokun3/ucm2'


def check():
    result = {'processes': {}}
    for service in ('pipewire', 'wireplumber'):
        pid = subprocess.check_output(['systemctl', '--user', 'show', service,
                                       '-p', 'MainPID', '--value'], text=True).strip()
        if pid == '0':
            raise RuntimeError(service + ' is not running')
        environment = Path('/proc/' + pid + '/environ').read_bytes().split(b'\0')
        values = [v.split(b'=', 1)[1].decode() for v in environment
                  if v.startswith(b'ALSA_CONFIG_UCM2=')]
        if values != [EXPECTED_UCM]:
            raise RuntimeError(service + ' does not use the Gaokun UCM tree')
        result['processes'][service] = {'pid': pid, 'ucm': values[0]}

    with tempfile.TemporaryDirectory(prefix='gaokun-audio-check.') as directory:
        sample = Path(directory) / 'silence.raw'
        sample.write_bytes(bytes(48000 * 2 * 2 * 2))
        player = subprocess.Popen(['pw-play', '--raw', '--format=s16', '--rate=48000',
                                   '--channels=2', str(sample)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        running = set()
        try:
            for _ in range(40):
                if player.poll() is not None:
                    break
                for status in Path('/proc/asound').glob('card*/pcm*p/sub*/status'):
                    try:
                        if 'state: RUNNING' in status.read_text():
                            running.add(str(status))
                    except OSError:
                        pass
                time.sleep(0.1)
            _, stderr = player.communicate(timeout=5)
        finally:
            if player.poll() is None:
                player.kill()
                player.wait()
        if player.returncode or not running or stderr.strip():
            raise RuntimeError('PCM playback check failed: ' + stderr)
        result['running_playback_pcms'] = sorted(running)

    for process in result['processes'].values():
        log = subprocess.check_output(['journalctl', '-b', '_PID=' + process['pid'],
                                       '--no-pager', '-o', 'cat'], text=True)
        if 'error.ucm' in log or 'failed to import' in log:
            raise RuntimeError('UCM import error in the current audio process')
    result['ucm_errors'] = False
    result['audible_test'] = False
    return result


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
