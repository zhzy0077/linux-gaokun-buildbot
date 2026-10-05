# Linux 7.2.9 构建与验证

内核源码使用 [kernel.org stable](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/)
或 [gregkh/linux 镜像](https://github.com/gregkh/linux)，默认标签为 `v7.2.9`。

补丁链保留 Gaokun 的设备树、GPIO174、CPU 产品名和平台配置：

- 标准补丁按 `upstream`、`others`、`media`、`0099` 顺序应用。
- 7.2.9 已包含 PDC 范围扩展，删除重复补丁；GPIO175 的唤醒映射继续单独禁用。
- `others/0007` 恢复上游双 DSI PLL 初始化修复（`93c97bc8d85d`），修正该变更被撤销后
  Gaokun3 的白线/黑屏。标准内核已实机验证；此补丁限定本项目的双 DSI 面板用途，
  上游撤销它的原因是其他单 DSI 设备的回归。
- EL2 补丁在标准链后应用，适配异步 remoteproc attach 和上游已有的 PAS attach，
  保留 SMP2P stop 状态及资源回收检查。

完整链测试只复制涉及的文件到临时 Git 仓库，不修改输入源码：

```bash
GAOKUN_KERNEL_SRC=/path/to/pristine-linux-7.2.9 \
GAOKUN_UPSTREAM_DTS=/path/to/pristine-linux-7.2.9/arch/arm64/boot/dts/qcom/sc8280xp-huawei-gaokun3.dts \
GAOKUN_UPSTREAM_CPUINFO=/path/to/pristine-linux-7.2.9/arch/arm64/kernel/cpuinfo.c \
python3 -B -m unittest discover -s tests -v
```

需要 Git 和 C 编译器。测试覆盖标准/EL2 补丁应用、源码镜像一致性、CPU 名称的板型范围、
PAS attach 唯一性和 SMP2P stop 行为。RPM workflow 默认构建标准内核；`build_el2=true`
时额外构建 EL2 软件包。

带 PLL 修复的 Release 6 已在 Gaokun 上验证标准/EL2 启动、LUKS 解锁和 GNOME 显示。
EL2 内核报告所有 CPU 从 EL2 启动及 VHE 初始化成功；最小 KVM 虚拟 CPU 执行了 ARM64
指令并返回预期 MMIO 数据。ADSP/CDSP/SLPI 均成功 attached，FD690 硬件加速、静音 PCM
链路和防火墙检查通过。完整客户机系统、音频听感、蓝牙配对与挂起恢复分别验收。
