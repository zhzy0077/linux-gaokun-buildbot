# Fedora Gaokun3 平台 RPM

## 软件包

Fedora GNOME 安装使用 `kernel-gaokun3`、`kernel-modules-gaokun3`、
`linux-firmware-gaokun3` 和 `gaokun3-platform`；外部模块开发按需安装
`kernel-devel-gaokun3`。`libgtop2` 使用 Fedora 官方包。

内核提供 Anaconda 所需的 `kernel` capability，三个内核包的版本依赖保持一致。
Gaokun 板级补丁负责 GPIO174 的触屏 SPI 模式和原生 ARM64 的 CPU 产品名输出；
内核配置包含 FedoraWorkstation 防火墙所需的 NetBIOS conntrack helper。
GPU 固件由内核 RPM 的 dracut 配置显式收集。

[Anaconda 安装流程](../tools/installer/README.md)负责 LUKS、Btrfs、共享 ESP、
目标 UUID 和 systemd-boot。GPIO 的实现与验证方法见[触屏说明](touchscreen_spi_mode_zh.md)。

## 平台包内容

| 内容 | 安装位置 |
| --- | --- |
| 模块加载和依赖 | `/usr/lib/modules-load.d/gaokun3-*.conf`、`/usr/lib/modprobe.d/gaokun3-*.conf` |
| 蓝牙和 GDM 工具 | `/usr/libexec/gaokun3/`，配套 systemd 服务 |
| 显示默认值 | `/usr/share/gaokun3/monitors.xml`、`/etc/xdg/monitors.xml`、`/etc/skel/.config/monitors.xml` |
| PCM 覆盖 | `/etc/modprobe.d/dist-alsa.conf`，同时收集到 initramfs |
| 触屏调参 | `/usr/bin/touchscreen-tune`、`/usr/share/gaokun3/touchscreen-tuner/` |
| 音频 UCM | `/usr/share/gaokun3/ucm2/`，供 PipeWire 和 WirePlumber 使用 |

系统级 XDG 显示默认值覆盖 GNOME 的临时登录/初始设置账户，用户配置优先。
GDM helper 兼容静态 `gdm` / `Debian-gdm` home，保留已有配置；安装包不会重启 GDM。

蓝牙 helper 根据本机标识生成稳定地址，在 `hci_uart` 的 modprobe hook 中先准备 NVM，
再加载驱动。更新使用原子替换并保留原始备份，避免连带修改其他硬链接固件。
`hci_uart` 留到解锁后的根文件系统加载。补充固件的原始摘要见
[来源清单](firmware-wcn6855-source.json)。

UCM 通过符号链接引用 Fedora 的其余配置，单独替换 Gaokun 选择器。
PipeWire 和 WirePlumber 的 drop-in 都设置
`ALSA_CONFIG_UCM2=/usr/share/gaokun3/ucm2`；原 `alsa-ucm` 文件保持不变。
Fedora 调整 UCM 目录结构时，应重新构建平台包。
当前会话升级后可执行以下命令载入新配置：

```bash
systemctl --user daemon-reload
systemctl --user restart pipewire wireplumber pipewire-pulse
```

手动使用 `alsaucm` 时也需要设置同一环境变量。
工具来源与授权信息见项目 README；平台 spec 使用 `LicenseRef-Unknown` 标记待统一的工具授权。

## 构建

在 Fedora 44 环境中：

```bash
sudo dnf install rpm-build systemd-rpm-macros alsa-ucm tar gzip
WORKDIR="$PWD/build-platform" \
ARTIFACT_DIR="$PWD/build-platform/artifacts" \
bash scripts/ci/71_build_platform_rpm.sh
```

产物为 `noarch`。版本默认使用 UTC 日期，可通过 `PLATFORM_RPM_VERSION` 指定。
完整 RPM 流水线会调用该脚本，并在清单的 `packages.platform` 中记录产物。

## 验证

在仓库根目录运行；指定原版内核文件可同时验证补丁应用和 CPU 名称输出：

```bash
GAOKUN_UPSTREAM_DTS=/path/to/linux/arch/arm64/boot/dts/qcom/sc8280xp-huawei-gaokun3.dts \
GAOKUN_UPSTREAM_CPUINFO=/path/to/linux/arch/arm64/kernel/cpuinfo.c \
python3 -B -m unittest discover -s tests -v
```

C 测试需要编译器。Gaokun 上的附加检查：

```bash
python3 -B tests/native_platform_smoke.py /path/to/extracted-rpms
python3 -B tests/native_monitor_defaults.py
python3 -B tests/native_audio_smoke.py
```

显示测试使用独立的 Mutter 配置和虚拟显示；音频测试播放两秒静音并检查硬件 PCM 状态。
已实机验证 NVMe 加密启动、GNOME 登录与横屏、CPU 产品名、GPU 加速、防火墙启动、
蓝牙地址和静音 PCM 链路。听感、录音、蓝牙配对、挂起恢复及外设功能分别进行交互验收。
