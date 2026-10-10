# Candidate Mutter fix: inherited plane ownership

**Status: retained reference alternative. The selected test path is a
Gaokun3-only DPU primary-plane restriction (kernel Release 12), keeping system
Mutter unchanged. This candidate was not built as a Mutter package, installed,
or hardware-validated. This directory is outside the kernel patch series.**

## Confirmed failure

On Gaokun3 with Mutter `50.5-1.fc44` and the release-10 EL2 kernel, a connected
M27P20 makes the GDM/session transition intermittently fail. Kernel tracing
captured `drm_atomic_plane_check()` rejecting planes 43 and 49 with:

```
[PLANE:43:plane-0] switching CRTC directly
[PLANE:49:plane-1] switching CRTC directly
```

Both planes are primary planes usable by multiple CRTCs. The previous DRM
owner can leave them bound in a different order than Mutter's first-fit
selection. Clearing a plane and assigning it to another CRTC in **one** atomic
request does not create an intermediate disabled state: the kernel validates
the final request against the old active state and rejects the direct move.

The second captured reproduction recovered without removing the cable after
both CRTCs became disabled and then enabled again. This is recovery evidence,
not evidence that the unpatched login path is reliable.

## Candidate design

Base: GNOME Mutter 50.5, commit
`c28623c8d29c7b61ec744cdfdbec36f5e7faef1c`.

The patch records the initial `drmModePlane.crtc_id` and uses it once while
constructing `MetaCrtcKms` objects to seed their existing primary/cursor plane
assignments. It leaves `find_unassigned_plane()` and `is_plane_active()` intact.
Subsequent changes use Mutter's normal assignment/unassignment tracking.

Related upstream proposal:
[!5055: Preserve kernel plane ownership on startup](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/5055),
commit `09ba03540e0a318c0015deba536a7f6fbe12c7bd`, by Devanshi Bansal.
The proposal was still open when checked. This candidate uses its initial
ownership snapshot idea but imports that snapshot into existing tracking,
rather than replacing runtime ownership checks with a fixed startup value.
Mutter 50.5 already contains the earlier runtime-assignment protection from
!4850; that protection must remain intact.

## Checks completed

- Patch applies cleanly to the three affected files from the pinned 50.5 source.
- Native C test executes the actual candidate initializer alongside the
  unmodified 50.5 selection functions (GPL-2.0-or-later fixture with original
  copyright/license notice).
- Test demonstrates the old first-fit choice conflicts with inherited kernel
  ownership, then verifies the candidate preserves the two reversed primary
  bindings and a cursor binding.
- Tests cover subsequent compositor ownership, release of a formerly inherited
  plane, exclusion of already selected planes, CRTC compatibility, and ignoring
  overlay/unbound/unknown-owner planes.

Run:

```sh
python3 -m unittest discover -s tests -p test_mutter_plane_ownership.py -v
```

These are lightweight tests, not a full Mutter build. Next acceptance requires
building against Fedora's matching Mutter source package, running its relevant
tests, and checking GDM/user transitions, monitor reconfiguration and hotplug
on the hardware. No installed Mutter files or display preferences were changed.

## Separate fixes

- Lost USB-C disconnect notifications belong to the Gaokun UCSI kernel patch.
- DP audio requires device-tree sound links, AudioReach topology and UCM routes.
- The login/greeter plane-reassignment failure belongs to Mutter's handoff logic.

The downloaded release-11 kernel RPMs contain the UCSI fix; they do not contain
this Mutter candidate or the local DP-audio test configuration. Upgrades remain
on hold at the user's request.
