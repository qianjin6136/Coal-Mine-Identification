# 树莓派文件传输器（Ubuntu 22.04 + VS Code）

这是一个独立的小程序，用来把本仓库中的树莓派采集端从 Ubuntu 22.04 传到树莓派。程序只依赖 Python 3 和 `ssh`，不需要安装 Python 第三方包。

它只传输 `raspberry-pi-sensor-logger` 中的运行代码、安装脚本、服务模板和依赖清单。以下内容不会传输，也不会覆盖树莓派上已有的同名目录：

- `data/`：树莓派已经采集的数据；
- `logs/`：运行日志；
- `.venv/`：必须在树莓派上按 ARM 环境重新创建；
- `go2_inspection/`：这是 Ubuntu 上位机程序，不在树莓派运行。

## 使用前准备

Ubuntu 22.04 和树莓派应连接到同一网络，并在树莓派开启 SSH。先从 Ubuntu 终端确认可以登录：

```bash
ssh pi@raspberrypi.local
```

如果使用 IP 地址，例如：

```bash
ssh pi@192.168.1.50
```

如果 Ubuntu 缺少 SSH 客户端：

```bash
sudo apt update
sudo apt install openssh-client
```

## 在 VS Code 中使用（推荐）

1. 把整个 `Coal Mine Identification` 文件夹放在 Ubuntu 22.04 上，代码与数据目录不要只复制一半。
2. 在 VS Code 中选择“文件 → 打开文件夹”，单独打开 `pi-file-transfer`。
3. 打开“终端 → 运行任务”。
4. 第一次先运行 `1. 检查树莓派传输文件（不连接）`，检查清单。
5. 再运行 `2. 传输采集程序到树莓派`，按提示输入树莓派地址、SSH 用户名、端口和目录。
6. 终端出现主机指纹确认时输入 `yes`；出现密码提示时输入树莓派的 SSH 密码。
7. 核对传输摘要，输入 `y` 后开始上传。

默认值为：

```text
地址：raspberrypi.local
用户：pi
端口：22
目录：~/sensor-reader
```

实际安装树莓派系统时创建的用户名如果不是 `pi`，必须填写真实用户名。

## 使用固定配置

先复制示例配置：

```bash
cd pi-file-transfer
cp transfer-config.example.json transfer-config.json
```

编辑 `transfer-config.json` 后，在 VS Code 中运行任务 `3. 按配置文件传输到树莓派`。配置文件已被 `.gitignore` 忽略，不要在其中保存密码；程序会在终端安全地调用 SSH 密码提示。私钥登录可以增加：

```json
{
  "host": "192.168.1.50",
  "user": "pi",
  "port": 22,
  "remote_path": "~/sensor-reader",
  "identity_file": "~/.ssh/id_ed25519",
  "include_tests": false,
  "connect_timeout": 10
}
```

## 直接在 VS Code 终端运行

检查文件，不连接树莓派：

```bash
python3 transfer_to_pi.py --dry-run --list-files
```

传输：

```bash
python3 transfer_to_pi.py \
  --host 192.168.1.50 \
  --user pi \
  --remote-path '~/sensor-reader'
```

如需把测试也传到树莓派，加 `--include-tests`。脚本先在本机生成最小压缩包，显示 SHA-256 并等待确认；随后通过一条 SSH 连接上传，在树莓派上再次校验 SHA-256，通过后才解压。因此使用密码登录时通常只需要输入一次密码。

传输属于“覆盖更新”：本次包内的同名程序文件会更新，但不会自动删除旧版遗留文件，也不会清理采集数据。

## 首次传输后的树莓派操作

在 VS Code 终端或 SSH 终端登录树莓派：

```bash
ssh pi@192.168.1.50
cd ~/sensor-reader
./scripts/setup_raspberry_pi.sh
sudo reboot
```

重启后：

```bash
cd ~/sensor-reader
./scripts/diagnose_hardware.sh
.venv/bin/python -m sensor_logger --once --require-all-hardware
```

以后只更新代码时，重新运行传输任务，然后在树莓派执行：

```bash
cd ~/sensor-reader
.venv/bin/python -m pip install --editable .
sudo systemctl restart sensor-logger.service
```

## 命令帮助与测试

```bash
python3 transfer_to_pi.py --help
python3 -m unittest discover -s tests -v
```

这个工具不会自动执行 `sudo`、不会重启树莓派，也不会删除树莓派采集数据。
