# Loaded as interactive defaults, without --kickstart or --liveinst.
graphical
url --url=https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Everything/aarch64/os/
# Use a distinct ID: an explicit URL source disables Fedora's default 'updates' ID.
repo --name=gaokun-fedora44-updates --metalink=https://mirrors.fedoraproject.org/metalink?repo=updates-released-f44&arch=aarch64
repo --name=gaokun3 --baseurl=file:///opt/installer/repo
bootloader --disabled
rootpw --lock
firstboot --enable
services --enabled=gdm,NetworkManager --disabled=sshd

%pre --erroronfail --log=/tmp/gaokun-pre.log
/usr/bin/python3 /opt/installer/gaokun_installer.py preflight
%end

%include /run/gaokun-installer/storage.ks

%packages
@^workstation-product-environment
kernel-gaokun3
kernel-modules-gaokun3
linux-firmware-gaokun3
gaokun3-platform
systemd-boot-unsigned
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

%pre-install --erroronfail --log=/tmp/gaokun-pre-install.log
/usr/bin/python3 /opt/installer/gaokun_installer.py prepare-target
%end

%post --nochroot --erroronfail --log=/tmp/gaokun-post.log
/usr/bin/python3 /opt/installer/gaokun_installer.py finalize
%end
