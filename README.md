# 3Dmigoto/XXMI Model Importer for Blender

Blender 插件，用于导入 3Dmigoto/XXMI 格式的游戏 MOD 模型。

A Blender addon for importing 3Dmigoto/XXMI game mod models.

## 支持的游戏 / Supported Games

| 游戏 | 格式代号 | 状态 |
|------|----------|------|
| 原神 / Genshin Impact | XXMI | ✅ |
| 崩坏：星穹铁道 / Honkai: Star Rail | XXMI | ✅ |
| 绝区零 / Zenless Zone Zero | ZZMI | ✅ |
| 鸣潮 / Wuthering Waves | WWMI | ✅ |
| 明日方舟：终末地 / Arknights: Endfield | EFMI | ✅ |

## 功能 / Features

- 🎮 直接导入 `.ini` 文件，自动解析 `.buf`、`.ib`、`.dds`
- 📦 支持 ZIP / RAR / 7z 压缩包直接导入（自动解压）
- 📂 支持选择文件夹导入（自动扫描 `.ini` 文件）
- 🎮 **游戏格式选择器** — 自动检测或手动指定游戏，优化 UV 解析
- 🧩 自动按 draw call 分离网格部件
- 🎨 自动分配贴图（支持 ps-t0 ~ ps-t69、this = 等多种格式）
- 🔄 贴图变体切换（侧边栏面板）
- 📦 网格变体分组（手动分组 + 独立勾选显示/隐藏）
- 📤 一键导出贴图（DDS 自动转 PNG）
- 🔧 手动旋转 XYZ / 镜像 XYZ
- 🎯 UV 格式自动检测 + 手动选择（含游戏专用格式）
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

支持的导入方式：
- 选择 `.ini` 文件直接导入
- 选择 `.zip` / `.rar` / `.7z` 压缩包（自动解压后导入）
- 选择文件夹（自动扫描目录中的 `.ini` 文件）

### 导入选项

| 选项 | 说明 | 默认 |
|------|------|------|
| 游戏 / Game | 游戏格式选择（影响 UV 解析） | 自动检测 |
| 镜像 X/Y/Z | 翻转模型 | 关 |
| X/Y/Z 旋转 | 手动旋转角度 | 0° |
| 分离部件 | 每个 draw call 独立对象 | 开 |
| 加载贴图 | 加载并分配贴图 | 开 |
| UV 格式 | UV 坐标格式 | 自动检测 |

### 游戏格式选择

插件会自动从 INI 内容检测游戏格式。如果自动检测不准，可手动选择：

| 选项 | 游戏 | UV 特殊处理 |
|------|------|-------------|
| 自动检测 | — | 根据 INI 内容智能判断 |
| 崩铁 / Star Rail | 崩坏：星穹铁道 | 自动检测 hf/u16/f32 |
| 绝区零 / ZZZ | 绝区零 | 自动检测 hf/u16/f32 |
| 原神 / Genshin | 原神 | 自动检测 hf/u16/f32 |
| 鸣潮 / Wuthering Waves | 鸣潮 | TexCoord stride 16 → hf@0 |
| 终末地 / Endfield | 明日方舟：终末地 | VB1 stride 12 → f32@0 |

### UV 格式选择

自动检测可能选错，如贴图映射异常请手动选择：

| 格式 | 说明 |
|------|------|
| auto | 自动检测（结合游戏格式 + 空间连续性分析） |
| Half-float @0 / @4 | 16位浮点 UV |
| uint16 @0 / @2 / @4 | 16位整数归一化 UV |
| float32 @0 / @4 | 32位浮点 UV |
| 终末地 VB2 | 终末地专用（VB2 bytes 0-3, uint16, 跳过前9退化顶点） |

### 侧边栏面板

3D 视口按 N → 3Dmigoto 标签：

- **导入模型** — 快捷导入
- **导出贴图** — 一键导出所有贴图
- **贴图浏览器** — 选中对象后加载贴图列表，点击切换
- **网格变体** — 手动分组控制部件显隐

## 各游戏格式详解

### 原神 / 崩铁 (XXMI)

```
Position (vb0): stride 40, float32 × 3
Blend    (vb2): stride 32, float32 + uint32
Texcoord (vb1): stride 8/12/16/20, half-float / uint16 / float32
Index:          R32_UINT, 每部件独立 IB
```

- 使用 `match_first_index` 分割子网格
- 每个部件有独立的 Position / Blend / Texcoord 缓冲区
- 纹理通过 `ps-t0` ~ `ps-t2` 槽位分配

### 鸣潮 (WWMI)

```
Position (vb0): stride 12, float32 × 3
Vector   (vb1): stride 8,  R8G8B8A8_SNORM (法线/切线)
Texcoord (vb2): stride 16, R16G16_FLOAT (half-float UV)
Color    (vb3): stride 4,  R8G8B8A8_UNORM
Blend    (vb4): stride 8,  R8_UINT
Index:          R32_UINT, 全部件共享 IB + match_first_index
```

- 使用合并的 Position / TexCoord 缓冲区（非按部件拆分）
- 支持 ShapeKey（变形目标）
- UV 在 TexCoord 缓冲区 offset 0，half-float 格式

### 终末地 (EFMI)

```
Position (vb0): stride 16, float32 × 3 (+ 4字节其他)
Blend    (vb1): stride 12, float32 × 2 UV + 4字节其他  ← UV 在这里！
Texcoord (vb2): stride 12, 其他数据（非 UV）
Index:          R16_UINT / R32_UINT, 每部件独立 IB
```

- UV 在 **VB1（Blend 缓冲区）** 的 bytes 0-7，float32 格式
- VB2 名为 "Texcoord" 但实际不包含 UV 数据
- 使用 `drawindexedinstanced` 命令
- 使用 `ref` 语法引用资源（`ib = ref Resource_Component0_IB`）
- 支持 LOD 切换（`if $lod_level == 0` 条件分支）
- 使用 `Meshes/` 子目录存放缓冲区文件

## 文件结构支持

```
MOD文件夹/
├── Model.ini
├── *.buf / *.ib
├── *.dds
├── Meshes/              ← 终末地等格式使用子目录
│   ├── Component0_VB0.buf
│   └── Component0_IB.buf
└── Textures/
    ├── body/Blue.dds
    └── head/Base.dds
```

支持子目录结构，自动搜索 INI 所在目录及上级目录。

## 技术细节

### UV 自动检测算法

1. **评分阶段**：测试所有格式（half-float / uint16 / float32 × 多个偏移），统计 [0,1] 范围内的有效 UV 数量
2. **空间连续性裁决**：当多个格式得分接近时，用网格拓扑计算相邻顶点 UV 距离，选择最连续的格式
3. **常量数据过滤**：排除方差极低的格式（元数据，非 UV）
4. **游戏格式覆盖**：根据选定的游戏格式直接指定正确的 UV 格式（优先级最高）

### 缓冲区格式参考

| 游戏 | Position | Texcoord | Blend | Index |
|------|----------|----------|-------|-------|
| 原神/崩铁 | stride 40, f32 | stride 8-20, hf/u16/f32 | stride 32, f32 | R32 |
| 绝区零 | stride 40, f32 | stride 20-24, hf/u16/f32 | stride 32, f32 | R32 |
| 鸣潮 | stride 12, f32 | stride 16, hf@0 | stride 8, R8 | R32 |
| 终末地 | stride 16, f32 | stride 12, f32@0 (在VB1) | stride 12 | R16/R32 |

## 已知限制

- 不支持骨骼/动画
- 不支持 Blend Shape（变形目标）
- INI 条件逻辑不生效，所有 draw call 均导入
- 终末地 LOD 默认导入 LOD0

## 许可证 / License

MIT License
