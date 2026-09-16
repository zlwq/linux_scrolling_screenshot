# Linux Scrolling Screenshot

一个面向 Linux 桌面环境的滚动长截图工具。

程序通过 `evdev` 监听键盘、鼠标滚轮和触摸板，通过 `mss` 截取屏幕区域，并使用 OpenCV 的帧差、ORB 特征匹配和位移估计，在滚动过程中实时拼接长图。

## 使用方式

1. 运行程序。
2. 按 `S`。
3. 用鼠标框选需要截图的区域。
4. 使用鼠标滚轮或触摸板双指滚动页面。
5. 按 `Space` 结束采集。
6. 在弹出的保存窗口中选择最终 PNG 的保存位置。

默认文件名使用当前时间：

```text
20260916_103527.png
```

程序只保存最终完整长图。运行期间产生的缓存截图会在正常处理结束后自动删除。

---

## Python 依赖

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

最小 `requirements.txt`：

```text
evdev
mss
numpy
opencv-python
```

`tkinter` 通常不是 pip 软件包，而需要通过 Linux 发行版的软件仓库安装。

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv python3-tk
```

如果 OpenCV 报：

```text
ImportError: libGL.so.1
```

可以尝试：

```bash
sudo apt install libgl1
```

### Fedora

```bash
sudo dnf install python3 python3-pip python3-tkinter
```

### Arch Linux

```bash
sudo pacman -S python python-pip tk
```

不同发行版的软件包名称可能不同，请以本发行版的软件仓库为准。

---

## `/dev/input/event*` 权限

这是最常见的问题之一。

本程序使用 Linux `evdev` 直接读取：

```text
/dev/input/event*
```

因此普通用户可能没有访问键盘、鼠标或触摸板设备的权限。

典型错误包括：

```text
没有找到可读取的键盘设备
无法监听 S 键
无法监听空格键
没有找到可读取的鼠标/触摸设备
```

先检查：

```bash
ls -l /dev/input/event*
```

很多发行版会把这些设备分配给 `input` 组。如果你的系统采用这种方式，可以执行：

```bash
sudo usermod -aG input "$USER"
```

随后需要**注销当前桌面会话并重新登录**。

### 安全提示

`input` 组通常意味着用户能够读取原始键盘输入，因此属于较高权限。

对于正式部署，更推荐针对指定设备编写 udev 规则，而不是无条件开放所有输入设备。

不建议仅仅为了解决权限问题长期使用：

```bash
sudo python3 scrolling_screenshot_final.py
```

因为 root 运行桌面 GUI 程序还可能导致显示服务器权限、环境变量和输出文件所有权等额外问题。

---

## X11 与 Wayland

程序使用 `mss` 获取屏幕内容，因此桌面显示协议会直接影响程序是否能够工作。

检查当前桌面：

```bash
echo $XDG_SESSION_TYPE
```

### X11 / Xorg

如果输出：

```text
x11
```

这是当前程序最适合的运行环境。

### Wayland

Wayland 对屏幕捕获实施了更严格的安全限制。

某些 Wayland 桌面环境中可能发生：

- `mss` 无法初始化
- 截图黑屏
- 找不到有效显示器
- 只能捕获部分内容
- `DISPLAY` 不可用

当前程序没有实现 `xdg-desktop-portal` / PipeWire 原生截图接口，因此**不能保证所有 Wayland 环境都可以直接运行**。

如果遇到截图问题，可以先登录 Xorg/X11 会话测试。

---

## 必须存在桌面图形环境

本程序不是 headless 工具。

它依赖：

- 屏幕截图
- Tk 窗口
- 鼠标框选
- 图形文件保存对话框

因此下面这些环境默认不能保证正常运行：

- 没有桌面的 Linux Server
- Docker 容器
- 普通 SSH 会话
- 没有配置图形转发的远程环境

如果出现：

```text
no display name and no $DISPLAY environment variable
```

说明当前 Python 进程没有可用的图形显示环境。

---

## tkinter 缺失

如果出现：

```text
ModuleNotFoundError: No module named 'tkinter'
```

需要安装系统 Tk 支持。

Debian / Ubuntu：

```bash
sudo apt install python3-tk
```

Fedora：

```bash
sudo dnf install python3-tkinter
```

Arch Linux：

```bash
sudo pacman -S tk
```

---

## 多显示器限制

当前版本主要针对第一个显示器工作。

如果在第二或第三个显示器上使用，可能出现：

- 框选层出现在错误显示器
- 坐标存在偏移
- 截图区域与选择区域不一致

多显示器完整支持属于后续可以继续改进的功能。

---

## 鼠标和触摸板兼容性

程序通过 Linux `evdev` 获取输入事件。

鼠标滚轮主要识别：

```text
REL_WHEEL
REL_WHEEL_HI_RES
```

触摸板双指滚动主要依赖类似：

```text
ABS_MT_POSITION_X
ABS_MT_POSITION_Y
BTN_TOOL_DOUBLETAP
```

的多点触摸事件。

不同触摸板、驱动和桌面环境暴露出来的事件并不完全相同，因此可能出现：

- 鼠标滚轮正常，但是触摸板不工作
- 触摸板滚动被系统转换成普通滚轮事件
- 高精度滚轮一次产生大量事件
- 某些设备不向当前用户暴露多点触控事件

可以使用：

```bash
python -m evdev.evtest
```

观察设备实际产生的事件。

---

## S 和 Space 是全局按键

程序直接监听原始键盘设备。

因此：

- `S`：开始框选。
- `Space`：结束截图。

它们并不要求程序窗口当前获得焦点。

也就是说，在截图过程中，如果你在浏览器、编辑器等其他程序中按下空格键，截图流程同样会结束。

---

## 前 20 张截图决定动态区域

正常情况下：

1. 前 20 张截图用于检测真正发生滚动变化的区域。
2. 第 20 张到达后确定红框。
3. 红框从这一刻开始冻结。
4. 后续图片不会改变动态区域。
5. 程序开始边获取新帧、边计算位移、边实时维护长图。

因此建议开始截图以后立即正常滚动。

不要让前 20 张全部保持几乎完全相同的画面。

如果前 20 张存在大量动画、闪烁区域或者页面根本没有明显移动，动态窗口可能判断错误。

如果最终不足 20 张，程序会自动退回普通离线处理。

---

## 当前算法适合什么

比较适合：

- 网页
- 文档
- 聊天记录
- 表格
- 长列表
- 普通软件界面

算法目前主要假设：

- 运动方向以垂直滚动为主
- 页面结构相对稳定
- 相邻图片存在足够视觉特征
- 主要内容整体向上或向下移动

---

## 哪些内容可能影响拼接

以下内容可能干扰动态区域检测或 ORB 特征匹配：

- 视频
- GIF
- 自动播放动画
- 实时刷新图表
- 自动轮播图
- 闪烁光标
- Sticky Header
- Sticky Footer
- 悬浮按钮
- 半透明动态效果
- 页面滚动时重新排版
- 横向滚动
- 截图过程中改变页面缩放比例

页面中同时运动的东西越多，越容易干扰程序判断真正的滚动位移。

---

## ORB 特征不足

如果区域中主要是：

- 大片纯色
- 空白背景
- 很少的文字
- 很少的边缘和纹理

ORB 可能找不到足够特征。

程序能够自动忽略部分无法匹配的帧，例如：

```text
变化区域内没有足够 ORB 特征
符合垂直滚动模型的匹配点太少
```

但如果长期没有足够视觉特征，就无法可靠地计算滚动距离。

---

## `无法得到稳定的垂直位移`

这个错误意味着程序找到了匹配点，但这些匹配点无法对：

> 页面到底上下移动了多少像素？

给出足够一致的结果。

常见原因：

- 页面动画过多
- 两帧之间滚动距离太大
- 存在明显横向运动
- 页面包含大量重复图案
- ORB 匹配到了错误位置
- 页面正在重新布局

可以尝试：

1. 滚动慢一点。
2. 减少每次滚动距离。
3. 避免动画播放期间截图。
4. 重新截图一次。

---

## 超长截图与内存

第 20 张以后程序采用流式处理，因此不需要永久保留全部历史截图。

但是：

**最终长图本身仍然需要完整存在于内存中。**

随着图片越来越长：

- NumPy 数组越来越大
- RAM 占用持续增长
- PNG 编码时间增加
- OpenCV 保存压力增加

极端情况下可能出现：

```text
MemoryError
```

或者被 Linux OOM Killer 强制结束。

---

## 临时缓存

运行过程中会创建类似：

```text
.scrolling_cache_20260916_103527
```

的临时目录。

正常结束后程序会自动删除缓存。

但是下面的情况可能导致缓存没有机会清理：

- `kill -9`
- Python 解释器崩溃
- 系统断电
- 系统异常重启
- OOM Killer 直接杀死程序

如果确认程序已经停止，可以手动删除遗留的：

```text
.scrolling_cache_*
```

---


## 为什么不能说“支持任何 Linux”

Linux 发行版之间差异很大。

这个程序实际上依赖：

- Linux input subsystem
- `/dev/input/event*`
- 桌面图形环境
- 显示服务器
- Tk
- OpenCV 动态库
- 输入设备驱动
- Python 和第三方包

因此更准确的说法是：

> **本程序面向常规 Linux 桌面环境设计。不同发行版、桌面环境、Wayland/X11 配置和输入设备权限可能需要额外适配。**

---


## 当前已知限制

- 主要针对 Linux 桌面环境。
- 当前优先支持垂直滚动。
- 当前主要针对第一个显示器。
- Wayland 原生截图兼容性有限。
- 需要访问 `/dev/input/event*`。
- `S` / `Space` 是全局按键。
- 动画内容可能影响 Mask 和 ORB。

---
 
## Authors

Made with 🐾 by

**kuromi_serika × GPT 5.6 Sol**


但是想法本身仅靠vibecoding是弄不出来的。目前除我之外github上没有比我性能更好的。
不信你运行一下试试呗。

