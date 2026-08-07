# 3Dmigoto/XXMI Model Importer for Blender

Blender 插件，用于导入 3Dmigoto/XXMI 格式的游戏 MOD 模型。

🔗 GitHub: https://github.com/Lonelyplanet2005/blender-3dmigoto-importer

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
- 🔧 **贴图管理面板** — 查看所有贴图槽位、手动替换、切换 Alpha 模式
- 🎮 **游戏开关面板** — 解析 INI 变量，直接在 Blender 切换服饰/配件
- 📦 **多 MOD 支持** — 导入多个 MOD 互不干扰，贴图/开关/分组按 MOD 隔离
- 🦴 **顶点组权重导入** — 自动从 Blend 缓冲区读取骨骼权重
- 🦴 **骨骼管理面板** — MMD 标准骨骼与其他骨骼分列显示，一键定位/删除
- 🦴 **自动绑定骨架** — 一键绑定/自动匹配后自动添加 Armature 修改器
- 📥 **MMD 骨架提取** — 从 MMD tools 导入的模型中提取骨架
- 🔄 贴图变体切换（侧边栏面板）
- 📦 网格变体分组（按 MOD 分框 + 独立勾选显示/隐藏 + 高亮描边）
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
| 加载贴图 | 加载并分配 Diffuse 贴图 | 开 |
| 启用特殊贴图 | 导入 LightMap、Normal、FX 等特殊贴图 | 关 |
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

## 侧边栏面板

3D 视口按 N → 3Dmigoto 标签：

### 主面板
- **导入模型** — 快捷导入
- **导出贴图** — 一键导出所有贴图

### 贴图浏览器
- 点击贴图名 → 显示放大预览
- 点📥按钮 → 应用贴图到对象
- 支持搜索过滤

### 网格变体（双栏布局）
- **左侧**：分组列表 + 滚动条
- **右侧**：选中组的部件列表 + 滚动条
- 🔍 高亮描边按钮（橙色边框 + 视口聚焦）
- 全部显示 / 全部隐藏

### 游戏开关
- 解析 INI 中的 `global persist $变量` 生成开关
- 点击切换 → 自动显示/隐藏对应的网格部件
- 支持布尔开关和多值循环

### 贴图管理
- 查看当前材质的所有贴图槽位
- 📁 按钮手动替换贴图
- 🔲 按钮切换 Alpha 模式（NONE / STRAIGHT / PREMUL）
- 快速添加 Diffuse / Normal / LightMap / FX / Alpha

### 顶点组
- 🦴 一键绑定（重命名顶点组 + 自动添加 Armature 修改器）
- ✏️ 手动绑定（弹窗输入骨骼名和顶点组名）
- 🤖 自动匹配（按位置距离自动匹配所有顶点组到最近骨骼）
- 📥 提取MMD骨架（从 MMD tools 导入的模型中提取骨架）
- 🔍 点击顶点组名进入权重绘制模式查看权重

### 骨骼管理
- MMD 标准骨骼与其他骨骼**分列显示**
- 🔍 点击骨骼名定位到该骨骼（Pose Mode + 聚焦）
- 🗑️ 每根非 MMD 骨骼旁有删除按钮
- 一键删除所有非 MMD 骨骼

## DDS 贴图处理

### BC7 自动转换

原神等游戏的 DDS 贴图使用 BC7 压缩格式，Blender 可能无法正确加载。插件会：
- 检测 DDS 文件头（BC7_UNORM / BC7_SRGB / BC6H）
- 自动通过 Pillow 转换为 PNG
- Alpha 通道大面积为零时自动修复为不透明

### Alpha 通道

部分 DDS 贴图的 Alpha 通道用作遮罩（全透明），导致 Blender 中模型不可见：
- 使用贴图管理面板的 🔲 按钮手动切换 Alpha 模式
- NONE = 忽略 Alpha（显示 RGB 内容）
- STRAIGHT = 使用 Alpha 透明

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

## 更新日志 / Changelog

### v3.15.0 — 骨骼管理面板

- 🦴 **骨骼管理面板**：MMD 标准骨骼与其他骨骼分列显示，一目了然
- 🔍 **定位骨骼**：点击骨骼名进入 Pose Mode 并聚焦到该骨骼
- 🗑️ **删除单根骨骼**：每根非 MMD 骨骼旁有删除按钮
- 🗑️ **一键删除非MMD骨骼**：批量删除所有非标准骨骼
- 🔗 **自动绑定骨架**：一键绑定/自动匹配后自动添加 Armature 修改器 + 设置父子关系

### v3.14.x — MMD 骨架提取

- 📥 **提取MMD骨架**：从 MMD tools 导入的模型中提取骨架，只删 MMD 网格保留 MOD 模型
- 🦴 **顶点组权重导入**：自动从 Blend 缓冲区读取骨骼权重并创建顶点组
- 🔧 **骨骼索引偏移自动检测**：解决不同组件骨骼索引位置不同的问题
- 🔧 **R16 索引支持**：16位索引缓冲区不再崩溃
- 🔧 **镜像翻转面朝向修复**：镜像导入后面朝向正确

### v3.11.0 — 多贴图导入 & 材质修复

- 🎨 **启用特殊贴图开关**：导入时可选择是否加载 LightMap/Normal/FX 等特殊贴图
- 🔧 **空贴图节点修复**：没有贴图文件时也创建已连接的 Image Texture 节点
- 🔧 **drawindexedinstanced 支持**：终末地等游戏的实例化绘制命令
- 🔧 **ref 语法支持**：终末地 `ib = ref Resource_...` 格式
- 🔧 **条件赋值优化**：LOD 条件分支只取第一个赋值（LOD0）

### v3.10.3 — 材质节点修复

- 🔧 **空贴图节点修复**：没有贴图文件时也创建已连接的 Image Texture 节点（方便后续手动添加贴图）

### v3.10.2 — 游戏开关按 MOD 切换

- 🎮 **开关联动选中对象**：点“加载开关”自动加载选中对象所属 MOD 的开关变量
- 📍 **显示 MOD 名称**：面板顶部显示当前 MOD 名称

### v3.10.1 — 贴图浏览器恢复

- 🔄 **恢复简洁版贴图浏览器**：贴图列表回到单列表+滚动条样式

### v3.10.0 — 贴图浏览器按 MOD 分框

- 🖼️ **按 MOD 分框显示**：每个 MOD 的贴图独立展开/收起
- 🖱️ **点击预览/应用**：点贴图名预览，点📥应用到对象

### v3.9.0 — 网格变体面板重构

- 📐 **按 MOD 分框**：网格变体面板按 MOD 分组显示，不再混合
- 📝 **完整控制按钮**：每个分组有重命名、删除、添加选中、移除按钮
- 🔦 **高亮描边**：点击🔍按钮，对象获得橙色描边 + 视口聚焦

### v3.8.2 — 自动分组按 MOD 隔离

- 🏷️ **分组名带 MOD 前缀**：`[MOD名] $变量`，不同 MOD 的分组不冲突
- 🚫 **不再清空其他 MOD 分组**：每个 MOD 独立管理

### v3.8.1 — 贴图列表按 MOD 切换

- 🔄 **切换 MOD 贴图**：选中不同 MOD 的对象 → 加载贴图 → 显示对应 MOD 的贴图
- 🧹 **智能清理**：只删除 Blender 未加载的临时 PNG，不影响其他 MOD

### v3.8.0 — 多 MOD 支持

- 📦 **贴图列表追加**：导入多个 MOD 时贴图列表不覆盖，去重后追加
- 🗑️ **清空按钮**：手动清空贴图列表重新开始

### v3.7.2 — Alpha 管理

- 🔲 **Alpha 切换按钮**：贴图管理面板中手动切换 Alpha 模式（NONE/STRAIGHT/PREMUL）
- 🖼️ **预览图 Alpha 修复**：DDS 预览图自动修复 Alpha 通道

### v3.7.1 — 游戏开关联动

- 🎮 **开关 ↔ 网格联动**：点击开关自动显示/隐藏对应的网格部件
- 🏷️ **条件标签**：导入时自动解析 INI 条件并标记每个对象
- 📥 **自动加载开关**：导入时自动从 INI 解析变量

### v3.7.0 — 游戏开关面板

- 🎮 **游戏开关面板**：解析 INI 中的 `global persist` 变量，在 Blender 侧边栏生成开关
- ☑️ **布尔开关**：服饰部件显示/隐藏（上衣、裙子、袜子等）
- 🔄 **多值开关**：支持循环切换（0→1→2→0）
- 🔃 **重置按钮**：一键恢复所有开关为 INI 默认值

### v3.6.0 — 多贴图支持

- 🎨 **完整贴图导入**：自动从 INI 解析并导入 Diffuse、LightMap、Normal、FX 等所有贴图类型
- 🔍 **贴图类型自动识别**：根据文件名/资源名自动分类
- 🔧 **贴图管理面板**：查看所有贴图槽位、手动替换、Alpha 切换
- ⚡ **一键自动填充**：从 INI 解析贴图并批量应用

### v3.5.0 — 贴图预览

- 🖼️ **贴图放大预览**：点击贴图名显示放大预览图（DDS 自动转换显示）
- 👆 **点击预览/应用分离**：点贴图名 = 预览，点📥按钮 = 应用到对象

### v3.4.0 — 面板布局重构

- 📐 **双栏布局**：网格变体面板改为左侧选组 + 右侧显示部件
- 📜 **滚动条**：组列表和部件列表都支持滚动

### v3.3.0 — 网格变体高亮

- 🔦 **高亮描边**：点击🔍按钮，对象获得橙色描边 + 视口自动聚焦

### v3.2.0 — 性能与稳定性优化

- ⚡ NumPy 向量化读取（Position 提速 10-50x）
- ⚡ UV foreach_set（面数多时明显提速）
- ⚡ UV 检测 early-exit
- 🛡️ 裸 except 全部替换为 `except Exception`
- 🛡️ 导入前验证（检查 INI 可读性）
- 📊 进度条
- 🧹 临时文件统一管理
- 🔍 资源诊断（找不到文件时显示相似文件名建议）

### v3.1.0 — 游戏格式支持

- 🎮 游戏格式选择器（崩铁/绝区零/原神/鸣潮/终末地）
- 🎯 终末地 UV 支持（VB1 f32@0）
- 🎯 鸣潮 UV 支持（TexCoord hf@0）
- 📦 ZIP/RAR/7z 压缩包直接导入
- 📂 文件夹导入支持

### v3.0.0 — 基础功能

- 🎮 3Dmigoto/XXMI 模型导入
- 🧩 自动按 draw call 分离网格部件
- 🎨 自动分配 Diffuse 贴图
- 📦 网格变体分组
- 📤 一键导出贴图
- 🌐 中英双语界面

## 已知限制

- 不支持骨骼/动画
- 不支持 Blend Shape（变形目标）
- INI 条件逻辑通过游戏开关面板支持，但复杂嵌套条件可能不完整
- 终末地 LOD 默认导入 LOD0
- 部分 DDS 贴图的 Alpha 通道为遮罩数据（全透明），需手动在贴图管理面板切换 Alpha 模式

## 许可证 / License

MIT License
