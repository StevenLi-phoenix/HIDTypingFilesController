# HIDTypingFilesController

用树莓派（或任何带 USB gadget 的机器）模拟 **USB 键盘**，把文件"打"进一台**不能联网、不能插 U 盘**的机器里。

典型场景：目标机只开放了键盘输入。中文、二进制、大文件都没法直接敲，而 HID 键盘
只能按出可打印 ASCII、`\n`、`\r`、`\t`——一个中文字符就会让整次输入中断。

所以这套工具的思路是：**在 Mac 上把文件打包成纯 ASCII 文本 → 让树莓派把它当键盘敲进去 → 在目标机上用一个
Java 程序还原成原文件。**

```
Mac:  hid_pack.py  ──pack──▶  payload.txt  ──HID 打字──▶  目标机: Unpack.java  ──▶  原始文件
      (git 仓库/路径)          (ASCII, 逐行校验)              (先校验全部, 再落盘)
```

---

## 目录结构

| 路径 | 作用 | 跑在哪 |
|---|---|---|
| `main.py` | HID 键盘驱动：把文件逐字符按出去（默认 0.01s 按下 + 0.02s 释放） | 树莓派 |
| `type.sh` | 用 `sudo` + `.venv` 调 `main.py` 的包装脚本 | 树莓派 |
| `dry.py` | 只检查文件里有没有键盘打不出来的字符，不发送 | 树莓派 |
| `requirements.txt` | `python-dotenv`、`tqdm` | 树莓派 |
| `pi-standalone/` | 不依赖 venv / 不依赖网络的精简版（`main_lite.py` + `go.sh` + `check.sh`） | 树莓派 |
| `hid_pack.py` | 打包器：选中 git 仓库里的一批文件，压成可打字的 ASCII 文本 | Mac |
| `Unpack.java` / `Unpack.txt` | 解包器：JDK 8 单文件，校验通过才写盘（`.txt` 是同一份源码，方便直接敲） | 目标机 |
| `tests/` | 47 个端到端测试 | Mac |
| `CLAUDE.md` | 给 AI 助手看的简明流程说明 | — |

两个约束贯穿全项目：

- **只用 Python 标准库 + JDK 8 标准库**，不引入新依赖（目标机只有 JDK 8）。
- **仓库只读**：`hid_pack.py` 只从 git 里读文件，从不修改仓库。

---

## 标准流程

### 1. Mac 上打包

```bash
cd hid-pack
.venv/bin/python hid_pack.py --repo ../myrepo --from <基线tag> -n            # 先看清单和预计打字时长
.venv/bin/python hid_pack.py --repo ../myrepo --from <基线tag> -o /tmp/myrepo.txt
```

`-n` 只列文件、大小和打字时间估算，不写任何东西；确认没问题再正式生成。

### 2. 传到树莓派并"打字"

```bash
# 在树莓派上
sudo python3 main.py /tmp/myrepo.txt          # 需要 tqdm + python-dotenv 和 ~/.venv 时用 type.sh
sudo ./type.sh /tmp/myrepo.txt --batch        # --batch: 一次性写所有报文，最快
```

目标机一侧先用记事本（Notepad）新建一个空文件，把焦点放进去，等树莓派的倒计时结束后自动输入。

### 3. 目标机上解包

```bash
# JDK 11+：可以直接跑 .txt 源码
java --source 11 Unpack.txt myrepo.txt . --dry-run     # 只校验，不写盘
java --source 11 Unpack.txt myrepo.txt .

# JDK 8（没有源码启动器）
cp Unpack.txt Unpack.java && javac Unpack.java && java Unpack myrepo.txt .
```

`Unpack` 会**先校验每一行的 CRC32、行号连续性、整段 SHA-256，全部通过后才写文件**；被覆盖的旧文件默认
备份成 `文件名.bak.yyyyMMddHHmmss`。

---

## 选择要传哪些文件

```bash
# 基线 tag -> 当前工作区（含未跟踪文件，自动跳过 .gitignore 的）
hid_pack.py --repo ../myrepo --from <基线tag> -o /tmp/myrepo.txt

# 两个 ref 之间，内容从 git 对象读（工作区里没提交的改动不会混进去）
hid_pack.py --repo ../myrepo --from <旧tag> --to HEAD -o /tmp/myrepo.txt

# 不用 --from：pathspec 覆盖到的所有文件
hid_pack.py --repo ../myrepo src ui/src -o /tmp/myrepo.txt

# 只挑"纯文本流程打不出来"的文件（中文/二进制）
hid_pack.py --repo ../myrepo --from <基线tag> --only-untypeable -o /tmp/myrepo.txt
```

- 末尾的 pathspec 可以收窄范围，例如 `':(exclude)tests' ':(exclude)docs'` 用来压缩体积。
- 以下文件**永不打包**，只给警告：看着像密钥的（`.env*`、`*.pem`、`*.key`、`*.jks`、`credentials.*`、
  `secrets/` 等），以及 **`mock/` 目录下的 `*.sql`**（本地开发用，可能 `DROP` 共享表）。
- 超过 9999 行会直接报 "too large"（行号只有 4 位）。

### 大交付：拆成多份

```bash
hid_pack.py --repo ../myrepo --from <基线tag> --split-by module -o /tmp/myrepo-parts
```

`--split-by module` 会按顶层模块生成一组编号文件（`1-src.txt`、`2-ui.txt`…，根目录文件归 `root`），
它们共享同一个 `pack-set` id。把这一整个目录敲进目标机上的同一个文件夹，然后让 `Unpack` 指向**目录**：

```bash
java --source 11 Unpack.txt /tmp/myrepo-parts .
# -> OK 1 (src) ... / FAILED 5 (ui) ... / MISSING 6 (docs) ...
```

好的一份照常应用，缺的/打错的会被点名（退出码 2），只需要重敲那一份，不用整包重来。

### 首次交付（目标机只有基础框架）

```bash
hid_pack.py --repo ../myrepo --from <基线tag> --init -o /tmp/myrepo-init
#   /tmp/myrepo-init/1-sql.txt              新增的 *.sql，最先敲、最先执行
#   /tmp/myrepo-init/2-code.txt             其余所有新文件，内含 hid-notes/framework-changes.txt
#   /tmp/myrepo-init/framework-changes.txt  同样的改动清单，UTF-8，在 Mac 上看
```

`--init` 只传**新增文件**。被修改过的基础框架文件、被删除的文件都不传，而是连 diff 一起写进
`framework-changes.txt`，到目标机上手工合并。

---

## 解包器参数

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只校验并打印会做什么，不写盘 |
| `--no-backup` | 覆盖时不备份 |
| `--apply-deletes` | 真正删除文件（默认只列出删除项） |
| 目录参数 | 把目录里所有分段当作一个包集处理，逐份报告 `OK`/`FAILED`/`MISSING` |

退出码：`0` 成功 / `1` IO 错误 / `2` payload 有问题。

记事本存成 UTF-8、UTF-8 with BOM 或"Unicode"（UTF-16 LE/BE）都能读；CRLF、缩进、空行都不影响。

---

## payload 格式（v1）

```
-----BEGIN HID PAYLOAD-----
version/source/created/from/to/mode/files/deletes/raw-bytes/gz-bytes/lines/sha256: ...
pack-id/pack-count/pack-label/pack-labels/pack-set: ...     <- 只有分段包才有
0001<256 个 base64 字符><crc32 hex8>                        <- 行号、数据、该行 CRC32
...
-----END HID PAYLOAD-----
```

- 每一行 = 4 位序号 + 256 个 base64 字符 + 8 位 CRC32，整段数据是一个 gzip 流。
- 内容里每个条目是 `[类型:1][路径长:4][路径 utf8][内容长:4][内容]`，类型 1=文件、2=可执行、3=删除、0=结束。
- 打错一行会按序号报出来（checksum），漏行报 "seq NNNN is missing"，尾巴被截断报 "no END marker"。
- 拒绝绝对路径、`..`、反斜杠和带 `:` 的路径。
- 头部字段是向后兼容的：老 payload 没有 `pack-*` 也能解。

---

## 注意事项

- **Caps Lock 必须关**：base64 区分大小写，开着 Caps Lock 会让每一行校验失败。
- 中文**文件名**需要目标机用 UTF-8 编码（Linux 上 `LC_ALL=C.UTF-8`），`Unpack` 检测到会提示。
- `Unpack.java` 本身也要手敲：保持纯 ASCII、不含 tab、长度约 14k 字符（约 7 分钟）。
  用纯文本编辑器或 heredoc 敲，别用会自动补括号的 IDE。
- HID 速度约 **0.03 秒/字符**（HIDcontroller 默认值）。压缩后的 payload 比直接敲 ASCII 文件划算得多，
  所以什么都应该打包。正式敲之前一定先 `-n` 或 `--dry-run` 看清单和耗时。
- 带固定主键的 seed SQL **不能重复执行**，目标库若已占用同样的 id 会冲突，执行前先确认。
- 如果这次交付包含框架改动，`framework-changes.txt` 是这些改动唯一的载体，务必合并。

---

## 树莓派端

`pi-standalone/` 是给"没有网络、没有 venv、SD 卡快满"的树莓派准备的：

```sh
python3 main_lite.py payload.txt --dry-run   # 只检查字符，不发送
sudo sh go.sh                                # 等主机识别键盘，倒计时 20 秒后开始敲
sudo sh go.sh payload.txt 30                 # 自定义文件 / 倒计时
sh check.sh                                  # 只读诊断：UDC 状态、磁盘占用、大目录
```

它只依赖 `python3` 标准库，不需要 `pip`、不需要 ssh。payload 文件放在同目录下叫 `payload.txt` 即可
（属于项目数据，不要提交到仓库）。

---

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

47 个测试，需要 `git` 和 JDK（会用 `javac --release 8` 编译 `Unpack.java`）。覆盖打包→解包全流程、
坏行/漏行/截断、路径穿越、BOM/UTF-16、分段包集、`--init` 等场景。
