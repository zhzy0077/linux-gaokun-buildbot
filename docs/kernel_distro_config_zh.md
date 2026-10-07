# Fedora / Ubuntu 内核配置

源码、Gaokun defconfig、设备树和补丁共用。发行版策略在编译前叠加，不能在已编译
Image 的 RPM/DEB 封装阶段修改。

| 目标 | 配置片段 | 默认主安全模块 | 包版本 |
| --- | --- | --- | --- |
| Fedora RPM | `defconfig/distro/fedora.config` | SELinux | 内核/模块/devel Release 9 |
| Ubuntu DEB | `defconfig/distro/ubuntu.config` | AppArmor | 内核/模块/headers revision 2 |

两者先合并 `common.config`：启用 usercopy、FORTIFY、SLAB freelist 加固与随机化、
分配时清零，以及 Landlock、Yama、Lockdown 支持。Lockdown 不强制开启。
这些片段是本项目的发行版安全策略，不是完整复制 Fedora/Ubuntu 官方内核配置。
本轮不改变调度、页大小、ACPI、板级驱动、EL2、TPM 命令限制，也不启用 IMA/PCR
测量或模块签名。Secure Boot 仍受固件限制。

## 构建路径

RPM workflow 固定 `KERNEL_DISTRO=fedora`，DEB workflow 固定 `KERNEL_DISTRO=ubuntu`。
标准及 EL2 变体都调用 `scripts/ci/configure_kernel.sh`：

1. 在各自输出目录生成 `gaokun3_defconfig`。
2. 用内核 `merge_config.sh -m` 合并共同及发行版片段。
3. 设置变体 LOCALVERSION，然后运行 `olddefconfig`。
4. `check-kernel-config.py` 验证片段中的每一个要求确实生效，否则停止构建。
5. 写入输出目录的 `gaokun-distro`；RPM/DEB 打包再次检查发行版和最终配置，拒绝混用。

发布清单包含 `kernel_distro`。实际 `.config` 仍随内核包安装到 `/boot/config-*`。
手工运行 `20_build_kernel_variants.sh` 时必须显式指定 `KERNEL_DISTRO`。

配置验证（不编译完整 Image）：

```bash
GAOKUN_DIR="$PWD" CROSS_COMPILE=aarch64-linux-gnu- \
  bash scripts/ci/configure_kernel.sh /path/to/patched-linux /build/fedora fedora
```

## 现有系统升级

从旧 Gaokun 内核升级至 Fedora 配置前，必须确认 SELinux 策略包和文件标签。
此前 SELinux 未激活的系统可能需要重标记；应保留可启动回退，并以 permissive
模式验证 AVC 和登录流程后再启用 enforcing。RPM 不自动改写已有管理员启动参数，
也不自动在运行中的文件系统上强制切换策略。

配置矩阵在应用完整标准/EL2 补丁的 Linux 7.2.9 上通过 `olddefconfig` 和最终选项
检查；完整内核构建及 Fedora/Ubuntu 实机安全策略验收另行验证。
