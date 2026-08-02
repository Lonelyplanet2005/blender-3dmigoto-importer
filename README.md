# 3Dmigoto/XXMI Model Importer for Blender

Blender 插件，用于导入 3Dmigoto/XXMI 格式的游戏 MOD 模型。

A Blender addon for importing 3Dmigoto/XXMI game mod models.

## 此项目为100%AI项目

## 支持的游戏 / Supported Games

- 绝区零 / Zenless Zone Zero
- 原神 / Genshin Impact
- 崩坏：星穹铁道 / Honkai: Star Rail
- 鸣潮 / Wuthering Waves

## 功能 / Features

- 🎮 直接导入 `.ini` 文件，自动解析 `.buf`、`.ib`、`.dds`
- 🧩 自动按 draw call 分离网格部件
- 🎨 自动分配贴图（支持 ps-t0、this = 等多种格式）
- 🔄 贴图变体切换（侧边栏面板）
- 📦 网格变体分组（手动分组 + 独立勾选显示/隐藏）
- 📤 一键导出贴图（DDS 自动转 PNG）
- 🔧 手动旋转 XYZ / 镜像 XYZ
- 🎯 UV 格式自动检测 + 手动选择
- 🌐 中英双语界面

## 安装 / Installation

1. 下载 `io_import_3dmigoto.py`
2. Blender → Edit → Preferences → Add-ons → Install
3. 勾选启用

### 安装 Pillow（推荐，用于 DDS 转换）

Blender → Scripting → 新建脚本 → 运行：

```python
import subprocess, sys
subprocess.call([sys.executable, '-m', 'pip', 'install', 'Pillow'])
```

## 使用 / Usage

### 导入模型

```
File → Import → 3Dmigoto 模型 (.ini)
```

### 导入选项

| 选项 | 说明 | 默认 |
|------|------|------|
| 镜像 X/Y/Z | 翻转模型 | 关 |
| X/Y/Z 旋转 | 手动旋转角度 | 0° |
| 分离部件 | 每个 draw call 独立对象 | 开 |
| 加载贴图 | 加载并分配贴图 | 开 |
| UV 格式 | UV 坐标格式 | 自动检测 |

### UV 格式选择

自动检测可能选错，如贴图映射异常请手动选择：

| 格式 | 适用场景 |
|------|----------|
| auto | 自动检测（可能不准） |
| hf0 / hf4 | Half-float 格式 |
| u16_0 / u16_2 / u16_4 | uint16 UNORM 格式 |
| f32_4 / f32_0 | float32 格式 |

### 侧边栏面板

3D 视口按 N → 3Dmigoto 标签：

- **导入模型** — 快捷导入
- **导出贴图** — 一键导出所有贴图
- **贴图浏览器** — 选中对象后加载贴图列表，点击切换
- **网格变体** — 手动分组控制部件显隐

## 文件结构支持

```
MOD文件夹/
├── Model.ini
├── *.buf / *.ib
├── *.dds
└── textures/
    ├── body/Blue.dds
    └── head/Base.dds
```

支持子目录结构，自动搜索 INI 所在目录及上级目录。

## 技术细节

| 缓冲类型 | Stride | 格式 |
|----------|--------|------|
| Position | 40 | 3×float32 XYZ |
| Texcoord | 8/16/20/24 | half-float / uint16 / float32 |
| Blend | 32 | 4×float32 + 4×uint32 |
| Index | 4 | R32_UINT |

## 已知限制

- 不支持骨骼/动画
- 不支持 Blend Shape
- INI 条件逻辑不生效，所有 draw call 均导入

## 许可证 / License

MIT License
