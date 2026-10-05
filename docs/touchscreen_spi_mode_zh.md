# Gaokun3 触摸屏的 SPI 模式选择

## 故障与验证

同一台 Gaokun3 上，原 NVMe Fedora 可以触控，而多个原始 USB 镜像不能触控。
故障时 Himax 芯片识别、固件重载及 SPI 传输均能完成，但没有触摸事件。

实机验证发现 GPIO 174 为输出高电平。保持它为低电平，再调用当前 SPI 驱动的
`inplace_reset` 后，触控恢复，内核开始报告坐标。此测试只改变模式选择脚和触控
初始化状态，根文件系统仍在 U 盘上。

上游资料：

- [right-0903/linux-gaokun：Touchscreen](https://github.com/right-0903/linux-gaokun#touchscreen)
  说明 GPIO 174 在固件重载前选择接口：低电平使用 SPI 原始触摸数据，高电平使用 I²C HID。
- [Touchscreen Research Notes](https://github.com/whitelewi1-ctrl/matebook-e-go-linux/blob/master/docs/TOUCHSCREEN.md)
  记录 UEFI 初始化路径差异，以及模式引脚曾由 UEFI 遗留状态决定的问题。
  其中的旧 I²C 恢复服务已被标为废弃；本修复使用当前 SPI 驱动自身的复位流程。

## 实现

设备树的 `touchscreen@0` 声明：

```dts
reset-gpios = <&tlmm 99 GPIO_ACTIVE_LOW>;
mode-gpios = <&tlmm 174 GPIO_ACTIVE_HIGH>;
```

`mode-gpios` 的极性刻意使用 `GPIO_ACTIVE_HIGH`，因此驱动写逻辑 `0` 就是物理低电平。
GPIO 控制器通过 `&tlmm` 引用，不能把某次启动中的 `/dev/gpiochip4` 编号写死。

`himax-spi-core.c` 的行为：

1. probe 中用 `devm_gpiod_get_optional(..., "mode", GPIOD_OUT_LOW)` 接管模式引脚，随后才取得 reset GPIO。
2. `himax_pin_reset()` 在复位脉冲前重新写入低电平。probe、手动恢复、面板恢复路径均通过该函数。
3. GPIO 获取失败时返回原错误码，保留 `-EPROBE_DEFER`。
4. 旧设备树没有 `mode-gpios` 时保持兼容，并输出提示；持久修复需要搭配更新后的 DTB。

修复同步保存在：

- `drivers/touchscreen-hx83121a/himax-spi-core.c`
- `dts/sc8280xp-huawei-gaokun3.dts`
- `patches/others/0003-Input-touchscreen-add-Himax-HX83121A-SPI-driver.patch`
- `patches/0099-arm64-gaokun3-import-local-dts-and-defconfig.patch`

CI 从补丁集构建内核，因此源码镜像与补丁都需要同步。

## 验证

在仓库根目录运行：

```bash
python3 -B -m unittest discover -s tests -v
```

`test_gpio174_spi_mode.py` 检查：

- CI 补丁生成的驱动文件与源码镜像一致。
- GPIO 声明及极性正确。
- 使用实际的复位函数编译 C 测试，检查模式引脚在每次复位前被拉低，包括模拟恢复后引脚再次为高的情况。
- 可通过 `GAOKUN_UPSTREAM_DTS` 指定原版 v7.2-rc2 DTS，验证导入补丁及最终 DTS 镜像。

安装与运行测试需要匹配内核的模块和更新后的 DTB。检查项目包括：

- 模块为 aarch64，`vermagic` 与目标内核匹配。
- 编译后的 `mode-gpios` 指向 TLMM 的 174 号线，flags 为 0。
- USB 冷启动后，GPIO 174 由触摸驱动占用并保持输出低电平。
- 点击、滑动、多指，以及熄屏/亮屏和休眠恢复后触控正常。

## 实机验证范围

7.1 已验证 USB 冷启动触控恢复；7.2 已验证内核启动、GPIO174 由 `mode` 占用并保持低电平。
完整的多点触控和挂起恢复按上述项目进行交互验收。
