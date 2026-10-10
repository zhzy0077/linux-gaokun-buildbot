# Preserved Gaokun DP audio experiment

This directory preserves the UCM configuration that produced user-confirmed
M27P20 monitor audio on the isolated release-10 EL2 test boot. It is an
**optional test payload**, not automatically installed by the RPM workflow.
The current driver-side display test must retain this audio configuration.

## Components and provenance

- `ucm2/`: the three tested verbs, their sound-model selector and DMI long-name
  selector. Base HiFi comes from `alsa-ucm-1.2.16.1-1.fc44`, upstream
  `alsa-project/alsa-ucm-conf` tag `v1.2.16.1`. Its original author notice and
  upstream BSD-3-Clause `LICENSE` are retained. DP variants add one verb-level
  mixer route and one device; existing internal audio definitions are unchanged.
- `../dp_audio_dtb.py`: prepare a fresh test DTB from the **new kernel's** DTB,
  adding eight nodes and changing only `/sound/model`. It reads phandles from
  the input, validates DAI argument counts, checks all existing properties and
  memory reservations after applying the overlay, and refuses existing outputs.
- Topology and firmware CPIO: already prepared in the local
  `gaokun-dp-audio-local/` experiment, recorded below. They are separate from
  this UCM payload and from the normal kernel RPM DTB.

| Port | CPU DAI | Codec | Topology stream / PCM | UCM verb / jack |
|---|---:|---|---|---|
| DP0 | 104 | `mdss0_dp0` / `ae90000` | MultiMedia5 / 4 | HiFiDP0 / DP0 Jack |
| DP1 | 129 | `mdss0_dp1` / `ae98000` | MultiMedia6 / 5 | HiFiDP1 / DP1 Jack |

The platform is q6apm. The sound model is
`SC8280XP-HUAWEI-GAOKUN3-DPAUDIO`; the actual ALSA long name is
`HUAWEI-GK_W7X-M1010-GK_W7X_PCB`. The long-name selector branches on CardName,
retaining the original configuration when booting the original sound model.

The original HiFi profile exposes internal audio only. A DP backend can reject
PCM preparation before ELD becomes valid, so each DP has a separate profile.
DP mixer routes belong in SectionVerb.EnableSequence, before ACP probes PCMs.
The real JackControl is present: the temporary jack bypass used to prove audio
output is not included here. Reliable hotplug/jack reporting still needs
acceptance with the UCSI-fixed kernel.

## Preserved local topology identity

Two DP graphs were derived from `linux-msm/audioreach-topology` commit
`b4c1510fe6686ab8053715a02c34bec875ca5c6c`, with sources/license retained in
`gaokun-dp-audio-local/topology-source/`. The merge preserves the original
24,296-byte topology's blocks except manifest counts, adding 11,428 bytes.
New container/subgraph IDs use 0x41xx and module IDs use 0x7xxx to avoid clashes.
Validation covered original controls/routes/PCMs, unique IDs and all module
references. No ADSP executable firmware was changed.

SHA-256 identities:

```
ee911c748deadc0191af50074b26ad177c8a2e78ff00e9578504b71d5d27b08a  original audioreach-tplg.bin
59b24207035077b7986b85827c9d081915b063ecf9b7462e6b5527ee24458bbb  dp-additions.bin
3229ba081f295c1739d918c12b4ec3a11469e59c3ed74fca44f9ed80f33dfa9e  SC8280XP-HUAWEI-GAOKUN3-DPAUDIO-tplg.bin
f041394f3361b5a148d4aedd158c3edef639099e78cf739f12180810ec613404  dp-audio-firmware.img
```

The firmware filename inside the additive CPIO is
`usr/lib/firmware/qcom/sc8280xp/SC8280XP-HUAWEI-GAOKUN3-DPAUDIO-tplg.bin`.
Keep the original topology file and original boot entry intact.

## Prepare for another kernel without installing it

Requires Python 3, libfdt, `dtc`, and `fdtoverlay`. Existing kernel-tree host
tools can be selected using `--dtc /path/to/dtc --fdtoverlay /path/to/fdtoverlay`;
this does not build a kernel.

```sh
python3 tools/audio/dp_audio_dtb.py \
  --dtb /path/to/new-kernel/sc8280xp-huawei-gaokun3-el2.dtb \
  --output /path/to/staging/sc8280xp-huawei-gaokun3-el2-dpaudio.dtb
```

The parent staging directory must already exist. The command writes only the
new output and prints its input/output hashes. It does not modify firmware,
UCM, packages, ESP files, boot selection or running audio services. Tested
against the old original DTB, it reproduces all properties and reservations
of the known-working audio DTB; run it again on the new build's DTB.

For a subsequently authorized boot test, use the matching new kernel and
modules, this generated DTB, and the verified firmware CPIO. The UCM tree needs
the common includes from the matching ALSA UCM package; this subtree alone is
not a complete replacement for `/usr/share/alsa/ucm2`. The existing experiment
has its additions in both ALSA and Gaokun-private UCM trees, with PipeWire and
WirePlumber selecting `/usr/share/gaokun3/ucm2`.

## Acceptance and rollback

Check both internal audio and the connected monitor, valid ELD/jack state,
exclusive PipeWire routing and the matching hardware PCM. Audible monitor
output is a separate user check. Also test connection after an unplugged boot:
ACP may need reprobe if the profile was unavailable during initial probing.

The new test boot is gated on explicit installation/reboot approval, enough
ESP space, and preserving the original kernel/modules/boot files. Selecting
the original sound model retains original UCM and topology. The local
experiment's hash-checked uninstall manifest remains the authority for
removing its already-installed additive files.
