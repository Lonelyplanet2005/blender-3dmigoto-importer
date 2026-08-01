# 3Dmigoto/XXMI Model Importer for Blender

Blender 插件，用于导入 3Dmigoto/XXMI 格式的游戏 MOD 模型（如绝区零、原神等）。

A Blender addon for importing 3Dmigoto/XXMI game mod models (Zenless Zone Zero, Genshin Impact, etc.)

## ⚠️此项目为100%AI项目

## ✨ 功能 / Features

- 🎮 直接导入 `.ini` 文件，自动解析所有关联的 `.buf`、`.ib`、`.dds` 文件
- 🧩 自动按 draw call 分离网格部件
- 🎨 自动加载贴图并创建 PBR 材质
- 🔄 支持变体贴图切换（如不同颜色的服装）
- 📁 自动搜索子目录结构
- 📦 DDS 自动转 PNG（需 Pillow）
- 🌐 中英双语界面

## 📥 安装 / Installation

1. 下载 `io_import_3dmigoto.py`
2. Blender → Edit → Preferences → Add-ons → Install... → 选择文件
3. 勾选启用

### 安装 Pillow（用于 DDS 贴图转换）

在 Blender 中：Scripting 工作区 → 新建脚本 → 运行：

```python
import subprocess, sys
subprocess.call([sys.executable, '-m', 'pip', 'install', 'Pillow'])
print("Done!")
```

## 🚀 使用方法 / Usage

### 导入模型

File → Import → 3Dmigoto 模型 (.ini) → 选择 MOD 包中的 `.ini` 文件

### 导入选项

| 选项 / Option | 说明 / Description | 默认 |
|---|---|---|
| 应用 -90° X 旋转 | 使模型在 Blender 中直立 | ✅ |
| 镜像 X 轴 | 左右翻转模型 | ❌ |
| 分离部件 | 每个 draw call 创建独立对象 | ✅ |
| 加载贴图 | 加载贴图并创建材质 | ✅ |

### 切换变体贴图

1. 3D 视口侧边栏（按 N）→ 3Dmigoto 变体
2. 点击 **扫描变体**
3. 点击切换不同颜色/样式

### 导出贴图

侧边栏 → **导出贴图** → 选择目标文件夹（DDS 自动转 PNG）

## 📂 支持的文件结构

```
MOD文件夹/
├── Model.ini              ← 选择此文件
├── Buffer/
│   ├── xxx-Position.buf
│   ├── xxx-Texcoord.buf
│   └── xxx-Component1.buf
├── Texture/
│   └── xxx_DiffuseMap.dds
└── textures/              ← 或这种结构
    ├── body/Base.dds
    └── head/Base.dds
```

## ⚠️ 已知限制

- 不支持骨骼/动画（仅静态网格）
- 不支持 Blend Shape
- INI 中的条件逻辑（`if $var == X`）不生效，所有 draw call 均导入
- 脸部模型可能不在 MOD 包中（仅覆盖贴图）

## 📋 支持的游戏 / Supported Games

理论上支持所有使用 3Dmigoto/XXMI 框架的 MOD，包括：

- 绝区零 / Zenless Zone Zero
- 原神 / Genshin Impact
- 崩坏：星穹铁道 / Honkai: Star Rail
- 鸣潮 / Wuthering Waves

## 🛠️ 技术细节

| 缓冲类型 | Stride | 数据 |
|---|---|---|
| Position | 40 | 3×float32 (XYZ) |
| Texcoord | 24/20 | half-float UV @ offset 4 |
| Blend | 32 | 4×float32 权重 + 4×uint32 骨骼 |
| Index | 4 | R32_UINT |

## 📄 许可证 / License

MIT License
