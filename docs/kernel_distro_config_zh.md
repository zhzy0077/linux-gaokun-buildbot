# 发行版默认内核配置与 Gaokun 覆盖

## 配置来源

RPM/DEB 使用完整官方 arm64 配置作为基线，不从精简 `gaokun3_defconfig` 补选功能。
源码、项目补丁和设备树继续共用；`gaokun3_defconfig` 保留作为板级参考和补丁镜像。

| 构建 | 固定基线 | 默认 LSM 策略 |
| --- | --- | --- |
| Fedora RPM | Fedora 44 `kernel-core-7.2.8-200.fc44.aarch64` | Fedora 原始 SELinux 列表 |
| Ubuntu DEB | Ubuntu 26.04 `linux-headers-7.0.0-38-generic` arm64，4 KiB | Ubuntu 原始 AppArmor 列表 |

原始配置保存在 `defconfig/baselines/`，未经改写。`sources.json` 固定包 URL、版本、
包内路径、包及配置 SHA256。Fedora 包已通过发行版 RPM 签名验证；Ubuntu 通过
Ubuntu Archive 2018 InRelease 签名、Packages.xz SHA256、DEB SHA256 逐层验证。
构建离线校验仓库内配置摘要，不随软件源的 latest 漂移。更换基线须重新审查差异。
包类型当前选择上述固定发行版基线；其他发行版版本的 rootfs 不自动切换内核基线。

## 合并顺序

1. 对未修改的官方配置运行 `olddefconfig`，生成 `vendor-normalized.config`。
2. 从原始官方配置重新开始，叠加 `defconfig/distro/common.config` 及对应发行版片段。
3. 叠加 `defconfig/gaokun3-required.config`；EL2 再叠加 `gaokun3-el2.config`。
4. `olddefconfig` 后逐项验证所有覆盖要求；不能生效即失败。
5. 编译前保存完整 `.config` 摘要与 `kernel-config-report.json`。打包再次核对配置、
   基线和片段摘要，拒绝将 Ubuntu 产物封装为 Fedora RPM，反之亦然。

工作流发布每个变体的 `.config` 和报告，清单包含 `kernel_distro` 及标准变体报告名称。
报告将变化分为：

- `baseline_normalization`：发行版内核到项目源树/工具链的差异，包括不存在的选项。
- `requirements`：人工覆盖的值、片段和原因。
- `board_delta`：相对已规范化基线的实际变化，区分显式覆盖与 Kconfig 依赖变化。

## 有意保留的例外

- **板级启动路径**：保留设备树而非固件 ACPI；保持验证过的早期存储、USB、时钟、
  中断、电源和 EC 驱动内建关系。所需父依赖也显式列入覆盖。
- **项目补丁能力**：保留 bonded DSI/PLL、DSC、HX83121A、GPIO174、摄像头、音频、
  蓝牙、QSEECOM、QRTR、远端 DSP 和 EL2/KVM 所需驱动。未为精简体积禁用其他厂商驱动。
- **TPM 验证边界**：QSEE TPM 模块保持 opt-in 和命令白名单；关闭 TPM2 HMAC、TPM
  hwrng，以及会扩展 PCR 的 IMA。其依赖变化在报告中可见。
- **构建证书**：清空源码中不存在的发行版信任/吊销证书文件路径；保留模块签名能力、
  自动签名和本次构建生成的签名密钥。该密钥不等于固件信任，不代表 Secure Boot 可用。
- **Ubuntu Binder**：发行版可模块化 Binder 的补丁不在主线内核中，因此转换成 `y`
  保留 Binder/BinderFS 能力，不将无效的 `m` 静默丢弃。
- **版本和本地版本名**：保留项目的标准/EL2 kernel release 命名；内核 RPM R10、DEB
  revision 3 与此前不同基线的产物区分。

其余安全、网络、存储、调度、内存管理、虚拟化和诊断设置继承发行版配置。版本变化、
发行版独有补丁与工具链仍可能改变可用选项，应审阅每次构建报告；不是完整发行版源码。
CI 提供 Rust 1.93.1、rust-src、bindgen 0.72.1、Clang/libclang、BTF/pahole 和 libdw，
并强制验证 Rust/BTF 未因缺少工具而被关闭。

## 验证和升级

```bash
GAOKUN_DIR="$PWD" CROSS_COMPILE=aarch64-linux-gnu- \
  bash scripts/ci/configure_kernel.sh /path/to/patched-linux /build/fedora fedora
```

完整标准/EL2 补丁的 Linux 7.2.9 已验证 Fedora/Ubuntu 四组配置生成、覆盖校验；
标准两组通过 `modules_prepare`，包括 Rust 内核库及 BTF 构建工具。完整 Image/模块
构建和实机回归仍需完成。完整发行版配置会显著增加模块数量，镜像容量需重新测量。

当前 Fedora 从 SELinux Disabled 升级前，要检查策略及文件标签，保留回退，以 permissive
验证重标记与登录后再启用 enforcing。RPM 不自动覆写管理员已有启动参数或重启设备。
