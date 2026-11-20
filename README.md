# BYC USB 烧录软件（BootLoader_Tools v3.5.7.1）

| 文件 | 说明 |
| --- | --- |
| `BootLoader_Tools_v3.5.7.1.exe` | 原版 |
| `BootLoader_Tools_v3.5.7.1_AutoDL.exe` | 增加了“自动下载模式”的版本 |
| `tools/add_auto_download.py` | 由原版生成 AutoDL 版和一键升级模板的补丁脚本（原程序没有源码，功能以二进制补丁方式加入） |
| `upgrader/Upgrader_Template.exe` | 一键升级程序的模板（本身不含固件，不直接使用） |
| `upgrader/make_upgrader.py` / `make_upgrader.bat` | 把固件 `.bin` 装进模板，生成双击即可升级的 exe |

## 自动下载模式

窗口底部多了一个复选框 **“自动下载模式（设备接入后自动开始下载）”**。

用法：

1. 点“打开文件”选好烧录文件；
2. 勾选“自动下载模式”；
3. 插入设备。之后每插入一只设备都会自动走完 切换 BOOT → 下载 → 校验 → 跳转 APP，
   不用再点“下载程序”。看到“程序更新成功！”后拔掉，换下一只即可。

行为说明：

- 设备以正常模式或 BOOT 模式接入都会触发。
- 没选烧录文件、或没勾选时，行为与原版完全一样。
- 刚烧完的设备跳回 APP 重新枚举时不会被再烧一遍（成功后 15 秒内的那一次接入会被忽略）。
- 下载失败不会自动重试，重新插拔设备才会再次触发。
- 勾选时已经连着的设备不会立刻开始下载，对它点一次“下载程序”或重新插拔。
- 勾选状态会记住：点击复选框时立即保存到注册表
  `HKEY_CURRENT_USER\Software\BYC\BootLoader_Tools` 的 `AutoDownload`，下次启动自动恢复（按 Windows 用户保存）。
- 烧录文件不会记住，每次启动后要重新“打开文件”；选好文件之前即使已勾选也不会触发下载。
- 点了“读取程序”时仍然执行读取，不会被自动下载抢走。

## 一键升级程序（把 .bin 做成双击即可升级的 exe）

生成（需要 Windows 上装有 Python 3，安装时勾选“Add python.exe to PATH”；只用标准库）：

- 把固件 `.bin` 拖到 `upgrader/make_upgrader.bat` 上（可以一次拖多个），会在 `.bin` 旁边生成
  `<名称>_升级.exe`。窗口会停住显示结果，按回车关闭；没装 Python 时也会提示。或者
- 命令行：

```bash
python upgrader/make_upgrader.py 固件.bin
```

可选参数：`-o 输出.exe` 指定输出文件，`--title "窗口标题"` 指定窗口标题（默认“固件升级 - <文件名>”）。

使用：把生成的 `<名称>_升级.exe` 发给使用者，双击打开，插入设备即自动升级，
提示“程序更新成功！”后拔出换下一台。不需要另外的 `.bin`，也不用点“打开文件”“下载程序”。

- 固件就附加在 exe 文件末尾，运行时程序以只读方式打开自身读取，不会解压出任何文件。
- 版本、校验和的显示，与芯片是否匹配的检查，以及下载、校验、跳转 APP 的流程，都和原版选文件烧录时一样。
- 打开程序时已经插着的设备会直接开始升级；刚升级完跳回 APP 的那次接入同样会被忽略（15 秒内）。
- 窗口里“打开文件”按钮被隐藏，“读取程序”“下载程序”按钮仍可用。
- 一个 `.bin` 对应一个升级程序；换固件请用原始的 `Upgrader_Template.exe` 重新生成，
  已经装过固件的 exe 不能再当模板用。
- 不要对生成的 exe 做数字签名，签名会附加在文件末尾，导致读不到固件。

## 重新生成

```bash
pip install pefile capstone
```

```bash
python3 tools/add_auto_download.py
```

```bash
python3 tools/add_auto_download.py --upgrader
```

第一条生成 AutoDL 版，第二条生成 `upgrader/Upgrader_Template.exe`。另需安装 `nasm`。脚本会校验原版 exe 的 SHA-256，补丁的具体内容见脚本开头的说明。
