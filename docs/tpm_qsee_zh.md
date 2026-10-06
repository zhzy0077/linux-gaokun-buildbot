# Gaokun3 QSEECOM fTPM 支持与验证

## 当前阶段

仓库提供 **默认关闭探测、限制命令的 fTPM 验证驱动**。已在 Gaokun3 实机 EL1 和
EL2/VHE 环境验证属性查询、SHA-256 PCR 读取和随机数请求。已完成 EL2 重启自动探测和
两轮 s2idle 恢复后的 TPM 验证，分别由键盘和 RTC 定时唤醒；RTC 轮次实测驻留约 40 秒。
此阶段不配置 LUKS TPM 自动解锁。

| 项目 | 实机结果 |
| --- | --- |
| 安全应用 | `qcom.tz.tpm`，由固件预加载 |
| 后端 | TPM 2.0，`QCOM` / `DPA fTPM` |
| 固件版本 | `0x30007` |
| 活跃 PCR bank | SHA-256，PCR 0–23 |
| 控制区发现 | `QUERY_INFO_2` 返回地址及 NV 存储大小；驱动不写死物理地址 |
| 设备节点 | `/dev/tpm0`、`/dev/tpmrm0` |

代码基于 Xilin Wu 的 [QSEE TPM 传输驱动](https://github.com/strongtz/linux-radxa-qcom/commit/9c3041295f115df392bf7675bd24685d152f18d4)
和 [SCM TPM 类型查询](https://github.com/strongtz/linux-radxa-qcom/commit/e66d8394348465af2c8948457cd62f8cb5b0d4da)，
在本仓库中增加 Gaokun3 板型范围、内存/协议边界检查和验证阶段的命令限制。
这些接口属于 QSEECOM；固件的 ACPI TPM2 表使用 start method 9，不能直接按通用
ACPI CRB 驱动的标准 SMC 方法 11 接入。

## EL2 故障与定向修正

驱动使用两类内存：

1. **内核新分配的 QSEECOM 请求/响应缓冲区**：继续使用 `qcom_tzmem` 的现有策略；
   EL2 下为 `SELF_OWNER`。
2. **固件预留的 TPM 控制区及命令/响应缓冲区**：仅对此固定区域使用
   `QCOM_SCM_VMID_HLOS` 注册 SHM bridge。

早期参考实现让两者都继承全局策略。在 EL2 下，SHM 注册和外层 QSEECOM 调用均返回成功，
但 TPM 控制区的 start 位不清零，响应头为空。HLOS 定向修正在同一次 EL2 启动中通过了
使用相同模块的 `SELF_OWNER → HLOS → SELF_OWNER → HLOS` 对照：结果依次为失败、成功、
失败、成功。两个成功条件分别通过三轮属性/PCR/随机数测试，PCR 值及更新计数保持稳定。

这说明固件固定区域的访问约定与 EL2 新分配缓冲区不同。修正位于 TPM 驱动，保持
`qcom_tzmem`、全局 DTB VMID、SMC 通道及 Secure Launch 逻辑原样。
`QSEECOM output_size=0` 在成功调用中也存在；有效 TPM 回复位于独立的固定响应区。

## 源码与打包

- 驱动镜像：`drivers/tpm-qcom-qsee/tpm_qcom_qsee.c`
- 内核补丁：`patches/others/0008-firmware-qcom-add-Gaokun-QSEE-TPM-validation.patch`
- `defconfig/gaokun3_defconfig` 与 `0099` 补丁中的配置镜像同步启用：

  ```text
  CONFIG_TCG_TPM=m
  CONFIG_TCG_QCOM_TPM_QSEE=m
  # CONFIG_TCG_TPM2_HMAC is not set
  # CONFIG_HW_RANDOM_TPM is not set
  ```

- 内核、模块、开发包 Release 同步为 **7**。现有 RPM 流水线自动包含新补丁及模块。
- 新增依赖只涉及内核 TPM/SCM 接口；本方案不添加用户态守护进程。

动态内存仍调用 `qcom_tzmem_alloc()`。固定区域直接调用已有的
`qcom_scm_shm_bridge_create()`，指定单个 HLOS VM 的读写权限，失败/卸载时使用匹配的
SCM 删除接口释放 bridge。地址必须对齐、无溢出、完整落在固件 NOMAP 风格区域内，且
不能与普通 System RAM 相交。命令长度、响应头、完成状态及缓冲区边界均有检查。

## 验证阶段限制

驱动仅接受 `huawei,gaokun3`，默认 `allow_probe=0`。测试时显式使用
`tpm_qcom_qsee.allow_probe=1` 内核参数，或手动加载：

```bash
sudo modprobe tpm_qcom_qsee allow_probe=1
sudo python3 -B tests/native_tpm_smoke.py
```

执行前需确认测试入口保留 TPM provisioning/PCR 写入服务的屏蔽配置。当前实机入口已
设置这些屏蔽；驱动级白名单另行保证仅允许：

- `SelfTest`、`GetTestResult`
- `GetCapability`
- `PCR_Read`
- `GetRandom`

`Startup`、`Shutdown`、`Clear`、NV 写入、层级/授权修改、PCR 扩展、密钥创建和封存均被
拒绝。`TCG_TPM2_HMAC` 保持关闭，避免 TPM 核心初始化创建临时主密钥；hwrng 集成也关闭。
该受限驱动暂不适用于正常密钥管理程序。卸载时 TPM 核心尝试发送 Shutdown 被拒绝的
`-EACCES` 日志，是当前限制的预期结果。

原生测试脚本只发送属性/PCR/随机数请求，不记录随机数字节。它支持固件一次只返回部分
PCR 的行为，要求每批取得进展、响应选择属于剩余请求，并且更新计数在分批读取期间一致。
选择 bank 时跳过未分配目标 PCR 的 bank，优先使用 SHA-256，其次 SHA-1。

## 已完成与待完成

已完成：

- 完整标准及 EL2 补丁序列在 pristine Linux 7.2.9 上应用。
- 驱动源码与补丁镜像一致，C 命令白名单及 HLOS bridge 参数测试通过。
- **60 项仓库测试通过，无跳过项**，包括共享桥成功/失败及零句柄清理、内存范围和 PCR 分批响应边界。
- 仓库源码编译出的清理版模块在当前 EL2 测试内核上通过属性、PCR、随机数验证。
- 2026-10-06 的 EL2 重启中自动出现 `/dev/tpm0`、`/dev/tpmrm0`，三轮验证通过。
- 两次 s2idle 挂起/恢复成功（失败计数 0）：第一次约 2 秒，由用户确认的键盘操作唤醒；
  第二次 RTC 定时唤醒，实测驻留 40.233 秒，RTC 事件计数增加。
  两轮恢复后各三次 TPM 检查通过，PCR 值和更新计数保持不变，测试闹钟已清除。
- 实机固件事件日志可读取；离线重算匹配 PCR0–4、6、7。

待完成：

- 更长时间待机及更多恢复周期的耐久性验证。
- PCR5 事件日志重算不匹配的原因。
- 放开验证限制前的 TPM 生命周期、授权和密钥管理设计。
- 完整 Release 7 RPM 构建/安装验收；当前设备使用独立测试内核中的更新模块。
- 后续 Secure Boot、UKI/PCR 策略和 LUKS TPM 注册，另行实施。

## 当前实机测试入口

默认入口：**Fedora 44 - TPM HLOS validation (7.2.9 EL2)**。
它使用 `7.2.9-gaokun3-el2-tpm1+` 独立模块目录，保持 TPM provisioning/PCR 写入服务屏蔽，
只对该入口打开 `allow_probe=1` 并移除 TPM 模块的自动加载黑名单。模块仍不在 initramfs 中，
LUKS 解锁继续使用原有密码。

已验证的 7.2.9 EL2 PLL-fix 入口及 EL1 对照入口保留。此次更新只替换测试内核目录中的
TPM 模块和测试 BLS 参数，没有重写内核 Image、initramfs 或 DTB，EFI 空闲仍约 49 MiB。
恢复时可在启动菜单选择原已验证入口；测试模块及 BLS 的原版备份位于
`/var/lib/gaokun-live-repairs/tpm-hlos-repo-20261006T010044Z/`。
