# pi-standalone：零依赖的树莓派输入端

根目录的 `main.py` 需要 `.venv` 里的 `tqdm` 和 `python-dotenv`，平时还要通过 ssh（也就是 Wi-Fi）来启动。
这个目录去掉了这两个前提：只要树莓派接着 USB 线、有 `python3`，就能把 payload 敲出去。

| 文件 | 用途 |
|---|---|
| `main_lite.py` | 与 `main.py` 相同的键位表，只用 Python 标准库；可等待键盘被识别、倒计时、写入超时 |
| `go.sh` | 等目标机识别键盘 → 倒计时 → 开始输入 |
| `check.sh` | 只读诊断：USB gadget（UDC）状态、`df -h`、占空间最多的目录、日志体积 |
| `payload.txt` | 要输入的 payload（`hid_pack.py` 的输出），只放在本地，不提交 |

## 使用

```sh
# 把 hid_pack.py 生成的 .txt 放到这里，命名为 payload.txt
python3 main_lite.py payload.txt --dry-run   # 只检查字符，不发送
sudo sh go.sh                                # 等键盘被识别，倒计时 20 秒后输入 payload.txt
sudo sh go.sh other.txt 30                   # 指定文件和倒计时秒数
```

`go.sh` 会先显示 `udc: not attached`，目标机识别键盘后变成 `udc: configured`，随后开始倒计时，
这段时间里把目标机的焦点切到文本编辑器。另一端没有主机时不会写入任何数据，也就不会卡死。

请在**不是目标窗口**的地方运行 `go.sh`（例如串口控制台），否则按键会打进自己的终端。

## 把文件放到树莓派上

按可用条件从好到坏：

1. **有网络**：`scp -r pi-standalone <pi 主机>:~/`
2. **没网络**：在 Mac 上把目录拷进 SD 卡的 FAT 启动分区（`/Volumes/bootfs`），再在树莓派上
   `sudo mount /boot; cp /boot/pi-standalone/* ~/`。FAT 分区不保留可执行权限，所以用 `sh go.sh`、
   `python3 main_lite.py` 运行，不要用 `./`。
3. **只需最少文件**：`main_lite.py` 加上你的 payload 就够了，可以直接从启动分区运行。

## SD 卡满了

`check.sh` 会显示 `df -h /`。根分区 100% 会导致树莓派开机后连不上 Wi-Fi（`wpa_supplicant` 写不了状态文件），
`main.py` 也会出现莫名其妙的错误。释放空间：

```sh
sudo journalctl --vacuum-size=50M
sudo rm -f /var/log/*.gz /var/log/*.1
rm -f ~/nohup.out ~/*.patch
```

卡满到一定程度可能无法启动（绿灯常亮、SD 卡无读写）。这时把卡插到 Mac 上离线清理，
或者直接换一张新刷的系统卡，这个目录不依赖旧的 `.venv`。
