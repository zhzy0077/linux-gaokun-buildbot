# Loaded as interactive defaults, without --kickstart or --liveinst.
graphical
url --url=file:///opt/installer/repo
# Filled by the media builder from the RPM-owned platform-cmdline file.
bootloader --sdboot --append="@PLATFORM_CMDLINE@"
rootpw --lock
firstboot --enable
services --enabled=gdm,NetworkManager --disabled=sshd

%pre --erroronfail --log=/tmp/gaokun-pre.log
/usr/bin/python3 /opt/installer/gaokun_installer.py preflight
%end

%packages
@^workstation-product-environment
kernel-gaokun3-el2
kernel-modules-gaokun3-el2
linux-firmware-gaokun3
gaokun3-platform
systemd-boot-unsigned
sdubby
efibootmgr
dracut
cryptsetup
btrfs-progs
alsa-ucm
alsa-utils
gnome-initial-setup
# Required for the complete PAM -> user manager -> GNOME login path.
systemd-pam
dbus-daemon
cracklib-dicts
openssh-server
firefox
langpacks-en
langpacks-zh_CN
-kernel
-kernel-core
-kernel-modules
-kernel-modules-core
-kernel-modules-extra
-linux-firmware
-atheros-firmware
-qcom-firmware
%end

%post --nochroot --erroronfail --log=/tmp/gaokun-post.log
/usr/bin/python3 /opt/installer/gaokun_installer.py validate
%end
