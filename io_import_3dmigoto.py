"""
3Dmigoto/XXMI Model Importer for Blender v1.0.3
=================================================
Import game mod models (.ini + .buf + .ib + .dds) into Blender.

Usage:
  Blender → Edit → Preferences → Add-ons → Install → Select this file
  Then: File → Import → 3Dmigoto Model (.ini)
"""

bl_info = {
    "name": "3Dmigoto/XXMI Model Importer",
    "author": "Lonelyplanet_",
    "version": (3, 15, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > 3Dmigoto Model (.ini)",
    "description": "Import 3Dmigoto/XXMI game mod models (Genshin/StarRail/ZZZ/WutheringWaves/Endfield)",
    "doc_url": "https://github.com/Lonelyplanet2005/blender-3dmigoto-importer",
    "tracker_url": "https://github.com/Lonelyplanet2005/blender-3dmigoto-importer/issues",
    "category": "Import-Export",
}

import bpy
import bpy.utils.previews
import bmesh
import struct
import os
import re
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty

# Module-level preview storage
_preview_collections = {}


# ============================================================
# Binary Readers
# ============================================================

def read_positions(filepath, stride=40, mirror_x=False, mirror_y=False, mirror_z=False, rot_x=0, rot_y=0, rot_z=0):
    """Read XYZ positions with optional rotation and mirror. Uses NumPy for speed."""
    import math
    import numpy as np
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride
    floats_per_vert = stride // 4

    # Parse all vertices at once, extract XYZ columns
    all_data = np.frombuffer(data, dtype=np.float32).reshape(n, floats_per_vert)
    positions = all_data[:, :3].copy()  # shape (n, 3)

    # Apply mirror
    mirror = np.array([
        -1.0 if mirror_x else 1.0,
        -1.0 if mirror_y else 1.0,
        -1.0 if mirror_z else 1.0
    ], dtype=np.float32)
    positions *= mirror

    # Apply rotation if needed
    if rot_x != 0 or rot_y != 0 or rot_z != 0:
        cx, sx = math.cos(rot_x), math.sin(rot_x)
        cy, sy = math.cos(rot_y), math.sin(rot_y)
        cz, sz = math.cos(rot_z), math.sin(rot_z)
        rot = np.array([
            [cx*cy, cx*sy*sz - sx*cz, cx*sy*cz + sx*sz],
            [sx*cy, sx*sy*sz + cx*cz, sx*sy*cz - cx*sz],
            [-sy,   cy*sz,             cy*cz]
        ], dtype=np.float32)
        positions = positions @ rot.T

    return [tuple(v) for v in positions]



def read_uvs(filepath, stride, hf_offset=None, uv_format="auto"):
    """Read UVs. Auto-detect format (half-float vs uint16 UNORM) and offset."""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride

    if hf_offset is not None:
        return [(u, 1.0 - v) for u, v in (struct.unpack_from('<ee', data, i * stride + hf_offset) for i in range(n))]

    # Manual format selection
    if uv_format != 'auto':
        fmt_map = {'hf0': ('hf',0), 'hf4': ('hf',4), 'u16_0': ('u16',0), 'u16_2': ('u16',2), 'u16_4': ('u16',4), 'u16_12': ('u16',12), 'f32_4': ('f32',4), 'f32_0': ('f32',0)}
        # Endfield split UV: U@0, V@4 (non-contiguous uint16)
        if uv_format == 'u16_split':
            print(f"  [Migoto] UV: manual format=u16_split (U@0, V@4)")
            out = []
            for i in range(n):
                try:
                    u = struct.unpack_from('<H', data, i * stride)[0] / 65535.0
                    v = struct.unpack_from('<H', data, i * stride + 4)[0] / 65535.0
                    out.append((u, 1.0 - v))
                except Exception:
                    out.append((0.0, 0.0))
            return out
        # Endfield VB2 UV: U@0, V@2 (uint16, skip first 9 degenerate vertices)
        if uv_format == 'ef_vb2':
            print(f"  [Migoto] UV: manual format=ef_vb2 (U@0, V@2, skip 9)")
            out = []
            for i in range(n):
                try:
                    if i < 9:
                        out.append((0.0, 0.0))
                    else:
                        u = struct.unpack_from('<H', data, i * stride)[0] / 65535.0
                        v = struct.unpack_from('<H', data, i * stride + 2)[0] / 65535.0
                        out.append((u, 1.0 - v))
                except Exception:
                    out.append((0.0, 0.0))
            return out
        if uv_format in fmt_map:
            fmt, off = fmt_map[uv_format]
            print(f"  [Migoto] UV: manual format={fmt}, offset={off}")
            out = []
            for i in range(n):
                try:
                    if off + 4 > stride:
                        out.append((0.0, 0.0))
                        continue
                    if fmt == 'hf':
                        u, v = struct.unpack_from('<ee', data, i * stride + off)
                    elif fmt == 'u16':
                        u16, v16 = struct.unpack_from('<HH', data, i * stride + off)
                        u, v = u16 / 65535.0, v16 / 65535.0
                    else:
                        u, v = struct.unpack_from('<2f', data, i * stride + off)
                    out.append((u, 1.0 - v))
                except Exception:
                    out.append((0.0, 0.0))
            return out

    # Auto-detect: test all formats: half-float, uint16 UNORM, float32
    candidates = []
    for offset in ([0, 4, 8] if stride <= 16 else [4, 0, 8]):
        if offset + 4 <= stride:
            candidates.append(('hf', offset))
    for offset in range(0, min(stride, 16), 2):
        if offset + 4 <= stride:
            candidates.append(('u16', offset))
    for offset in range(0, min(stride, 20), 4):
        if offset + 8 <= stride:
            candidates.append(('f32', offset))
    # Non-contiguous UV: U at bytes 0-1, V at bytes 4-5 (Endfield VB2 format, stride 12)
    if stride == 12:
        candidates.append(('u16_split', 0))

    # Phase 1: score by [0,1] validity (no coverage penalty - it misleads when UV range is narrow)
    scored = []  # (score, fmt, off)
    max_sample = min(2000, n)
    for fmt, off in candidates:
        score = 0
        for i in range(max_sample):
            try:
                if fmt == 'hf':
                    u, v = struct.unpack_from('<ee', data, i * stride + off)
                elif fmt == 'u16':
                    u16, v16 = struct.unpack_from('<HH', data, i * stride + off)
                    u, v = u16 / 65535.0, v16 / 65535.0
                elif fmt == 'u16_split':
                    u = struct.unpack_from('<H', data, i * stride)[0] / 65535.0
                    v = struct.unpack_from('<H', data, i * stride + 4)[0] / 65535.0
                else:
                    u, v = struct.unpack_from('<2f', data, i * stride + off)
                if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                    score += 1
            except Exception:
                pass
        scored.append((score, fmt, off))
        # Early exit: perfect score found, no need to test more
        if score >= max_sample:
            break

    # Phase 2: for top candidates, break ties using spatial coherence
    scored.sort(key=lambda x: -x[0])
    best_fmt, best_off = scored[0][1], scored[0][2]
    if len(scored) > 1 and n > 100:
        threshold = scored[0][0] * 0.9
        top = [(s, f, o) for s, f, o in scored if s >= threshold and s > 0]
        if len(top) > 1:
            best_coherence = float('inf')
            check = min(200, n - 1)  # Reduced from 500 for speed
            for _, fmt, off in top:
                total_dist = 0.0
                cnt = 0
                u_sum = v_sum = u_sq = v_sq = 0.0
                for i in range(check):
                    try:
                        if fmt == 'hf':
                            u0, v0 = struct.unpack_from('<ee', data, i * stride + off)
                            u1, v1 = struct.unpack_from('<ee', data, (i + 1) * stride + off)
                        elif fmt == 'u16':
                            u0 = struct.unpack_from('<H', data, i * stride + off)[0] / 65535.0
                            v0 = struct.unpack_from('<H', data, i * stride + off + 2)[0] / 65535.0
                            u1 = struct.unpack_from('<H', data, (i + 1) * stride + off)[0] / 65535.0
                            v1 = struct.unpack_from('<H', data, (i + 1) * stride + off + 2)[0] / 65535.0
                        elif fmt == 'u16_split':
                            u0 = struct.unpack_from('<H', data, i * stride)[0] / 65535.0
                            v0 = struct.unpack_from('<H', data, i * stride + 4)[0] / 65535.0
                            u1 = struct.unpack_from('<H', data, (i + 1) * stride)[0] / 65535.0
                            v1 = struct.unpack_from('<H', data, (i + 1) * stride + 4)[0] / 65535.0
                        else:
                            u0, v0 = struct.unpack_from('<2f', data, i * stride + off)
                            u1, v1 = struct.unpack_from('<2f', data, (i + 1) * stride + off)
                        if 0 <= u0 <= 1 and 0 <= v0 <= 1 and 0 <= u1 <= 1 and 0 <= v1 <= 1:
                            total_dist += abs(u0 - u1) + abs(v0 - v1)
                            u_sum += u0; v_sum += v0
                            u_sq += u0 * u0; v_sq += v0 * v0
                            cnt += 1
                    except Exception:
                        pass
                if cnt > 50:
                    # Skip constant data (metadata)
                    u_var = u_sq / cnt - (u_sum / cnt) ** 2
                    v_var = v_sq / cnt - (v_sum / cnt) ** 2
                    if u_var < 1e-6 or v_var < 1e-6:
                        continue
                    coherence = total_dist / cnt
                    if coherence < best_coherence:
                        best_coherence = coherence
                        best_fmt, best_off = fmt, off

    best_score = scored[0][0]
    print(f"  [Migoto] UV: stride={stride}, format={best_fmt}, offset={best_off} ({best_score}/{min(2000, n)} valid)")

    out = []
    for i in range(n):
        if best_fmt == 'hf':
            u, v = struct.unpack_from('<ee', data, i * stride + best_off)
        elif best_fmt == 'u16':
            u16, v16 = struct.unpack_from('<HH', data, i * stride + best_off)
            u, v = u16 / 65535.0, v16 / 65535.0
        elif best_fmt == 'u16_split':
            u = struct.unpack_from('<H', data, i * stride)[0] / 65535.0
            v = struct.unpack_from('<H', data, i * stride + 4)[0] / 65535.0
        else:  # f32
            u, v = struct.unpack_from('<2f', data, i * stride + best_off)
        out.append((u, 1.0 - v))
    return out


def read_indices(filepath, format=''):
    """Read index buffer. Supports R16_UINT (2-byte) and R32_UINT (4-byte) indices."""
    with open(filepath, 'rb') as f:
        data = f.read()
    fmt_lower = format.lower()
    if 'r16' in fmt_lower:
        return list(struct.unpack(f'<{len(data)//2}H', data))
    elif 'r32' in fmt_lower:
        return list(struct.unpack(f'<{len(data)//4}I', data))
    else:
        if len(data) % 4 != 0:
            return list(struct.unpack(f'<{len(data)//2}H', data))
        indices32 = list(struct.unpack(f'<{len(data)//4}I', data))
        if len(indices32) > 0 and max(indices32) > 10000000:
            return list(struct.unpack(f'<{len(data)//2}H', data))
        return indices32


def read_blend_weights(filepath, stride=32):
    """Read blend weights from buffer.
    Format: 4 x float32 weights + bone indices (offset varies by game/component).
    Returns list of [(bone_idx, weight), ...] per vertex.
    """
    if not filepath or not os.path.exists(filepath):
        return []
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride
    if n == 0:
        return []

    # Auto-detect bone index offset
    # Bone indices are uint32 values typically in range [1, 200]
    # They appear after the 4 x float32 weights (offset >= 16)
    bone_offset = 16  # default
    best_count = 0
    for off in range(16, min(stride - 3, 28), 4):
        count = 0
        for i in range(min(n, 500)):
            if i * stride + off + 4 > len(data):
                break
            v = struct.unpack_from('<I', data, i * stride + off)[0]
            if 0 < v < 200:
                count += 1
        if count > best_count:
            best_count = count
            bone_offset = off

    # If no valid bone indices found at any offset, try offset 20
    if best_count == 0:
        bone_offset = 20

    result = []
    for i in range(n):
        if i * stride + 16 > len(data):
            result.append([])
            continue
        weights = struct.unpack_from('<4f', data, i * stride)
        # Read up to 4 bone indices starting at detected offset
        max_bones = min(4, (stride - bone_offset) // 4, (len(data) - i * stride - bone_offset) // 4)
        bones = []
        for j in range(max_bones):
            try:
                b = struct.unpack_from('<I', data, i * stride + bone_offset + j * 4)[0]
                bones.append(b)
            except Exception:
                bones.append(0)

        vert_weights = []
        for w, b in zip(weights, bones):
            if w > 0.001 and b > 0:
                vert_weights.append((b, w))
        result.append(vert_weights)
    return result


# ============================================================
# INI Parser
# ============================================================

def _resolve_lookup(resolved, name):
    """Case-insensitive resource name lookup."""
    if not name:
        return None
    if name in resolved:
        return resolved[name]
    name_lower = name.lower()
    for k, v in resolved.items():
        if k.lower() == name_lower:
            return v
    return None


def parse_ini_toggles(ini_path):
    """Parse global persist variables from INI as toggle definitions.
    Returns list of {'name': str, 'var': str, 'default': int, 'max': int}
    """
    toggles = []
    seen = set()
    with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            s = line.strip()
            if not s.startswith('global persist'):
                continue
            m = re.match(r'global\s+persist\s+\$(\w+)\s*=\s*(-?\d+)', s)
            if not m:
                continue
            var_name = m.group(1)
            default = int(m.group(2))
            if var_name in seen:
                continue
            seen.add(var_name)
            if var_name in ('mx', 'my', 'menu', 'page'):
                continue
            display = var_name.replace('_', ' ').title()
            display_map = {
                'Top': '上衣 / Top', 'Skirt': '裙子 / Skirt', 'Hair': '发型 / Hair',
                'Body': '身体 / Body', 'Ears': '耳朵 / Ears', 'Tail': '尾巴 / Tail',
                'Neck': '颈部 / Neck', 'Mask': '面罩 / Mask', 'Sleeves': '袖子 / Sleeves',
                'Booba': '胸部 / Chest', 'Hat': '帽子 / Hat', 'Socks': '袜子 / Socks',
                'Sandal': '凉鞋 / Sandal', 'Obi': '腰带 / Obi', 'Color': '颜色 / Color',
                'Nails': '指甲 / Nails', 'Blush': '腮红 / Blush', 'Lips': '嘴唇 / Lips',
                'Shibari': '束缚 / Shibari', 'Pube': '体毛 / Body Hair',
                'Ofudatop': '御札上 / Ofuda Top', 'Ofudabottom': '御札下 / Ofuda Bottom',
                'Obiribbon': '腰带 ribbon / Obi Ribbon',
                'Wombtattoo': '腹部纹身 / Womb Tattoo',
                'Legaccessories': '腿部配件 / Leg Accessories',
                'Evachoker': '项圈 / Choker', 'Evabowtie': '领结 / Bowtie',
                'Evajacket': '夹克 / Jacket', 'Evaboots': '靴子 / Boots',
                'Evathighstrap': '大腿带 / Thigh Strap', 'Evaears': '兽耳 / Animal Ears',
                'Evalabubu': '拉布布 / Labubu',
            }
            display = display_map.get(var_name.capitalize(), display)
            toggles.append({
                'name': display,
                'var': var_name,
                'default': default,
                'max': 1,
            })

    # Scan draw calls to find multi-value variables
    in_condition = None
    var_values = {}  # var -> set of values
    with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            s = line.strip()
            if s.startswith('if ') or s.startswith('elif '):
                cond = s.split(' ', 1)[1]
                for vm in re.finditer(r'\$(\w+)\s*==\s*(\d+)', cond):
                    var_values.setdefault(vm.group(1), set()).add(int(vm.group(2)))
                for vm in re.finditer(r'\$(\w+)\s*!=\s*(\d+)', cond):
                    var_values.setdefault(vm.group(1), set()).add(int(vm.group(2)))

    # Update max for multi-value variables
    for t in toggles:
        if t['var'] in var_values:
            vals = var_values[t['var']]
            t['max'] = max(max(vals), 1)

    return toggles


def parse_condition(cond_str):
    """Parse a condition string like '$top == 1 && $socks != 0' into structured data.
    Returns list of (var_name, operator, value) tuples.
    """
    if not cond_str:
        return []
    parts = []
    for clause in re.split(r'&&|\|\|', cond_str):
        clause = clause.strip()
        m = re.match(r'\$(\w+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+)', clause)
        if m:
            parts.append((m.group(1), m.group(2), int(m.group(3))))
    return parts


def evaluate_condition(cond_parts, toggle_values):
    """Evaluate parsed condition parts against current toggle values.
    Returns True if condition is met (object should be visible).
    """
    if not cond_parts:
        return True  # No condition = always visible
    for var, op, val in cond_parts:
        current = toggle_values.get(var, 0)
        if op == '==' and current != val:
            return False
        elif op == '!=' and current == val:
            return False
        elif op == '>' and current <= val:
            return False
        elif op == '<' and current >= val:
            return False
        elif op == '>=' and current < val:
            return False
        elif op == '<=' and current > val:
            return False
    return True


def parse_ini_toggles_from_content(ini_content):
    """Parse toggles from INI content string (for already-read files)."""
    toggles = []
    seen = set()
    for line in ini_content.split('\n'):
        s = line.strip()
        if not s.startswith('global persist'):
            continue
        m = re.match(r'global\s+persist\s+\$(\w+)\s*=\s*(-?\d+)', s)
        if not m:
            continue
        var_name = m.group(1)
        default = int(m.group(2))
        if var_name in seen:
            continue
        seen.add(var_name)
        if var_name in ('mx', 'my', 'menu', 'page'):
            continue
        display = var_name.replace('_', ' ').title()
        toggles.append({'name': display, 'var': var_name, 'default': default, 'max': 1})
    return toggles


def build_component_texture_map(ini_path, ini_dir, resolved):
    """Parse INI to build section -> texture mapping.
    Handles both WWMI (this = ResourceXXX) and 3Dmigoto (ps-t0 = ResourceXXX) formats."""
    import re as _re
    
    resource_files = {}
    section_tex_refs = {}  # section_name -> [resource_names]
    current_section = None
    
    with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            s = line.strip()
            if s.startswith('[') and s.endswith(']'):
                current_section = s[1:-1]
                continue
            if '=' not in s or not current_section:
                continue
            key, val = s.split('=', 1)
            key, val = key.strip(), val.strip()
            
            if key == 'filename' and current_section.startswith('Resource'):
                resource_files[current_section] = val
            
            # WWMI format: this = ResourceTextureN
            if key == 'this' and val.startswith('Resource'):
                if current_section not in section_tex_refs:
                    section_tex_refs[current_section] = []
                section_tex_refs[current_section].append(val)
            
            # 3Dmigoto format: ps-t0 = ResourceXXX (diffuse)
            if key == 'ps-t0' and val.startswith('Resource'):
                if current_section not in section_tex_refs:
                    section_tex_refs[current_section] = []
                section_tex_refs[current_section].append(val)
    
    # Build section -> texture paths
    section_textures = {}
    for section, res_names in section_tex_refs.items():
        for res_name in res_names:
            # Try resolved lookup
            res_data = _resolve_lookup(resolved, res_name)
            if res_data and res_data.get('path') and os.path.exists(res_data['path']):
                if section not in section_textures:
                    section_textures[section] = []
                section_textures[section].append(res_data['path'])
                continue
            
            # Try resource filename
            fname = resource_files.get(res_name, '')
            if fname:
                fp = os.path.join(ini_dir, fname)
                if os.path.exists(fp):
                    if section not in section_textures:
                        section_textures[section] = []
                    section_textures[section].append(fp)
                    continue
            
            # Try to find by resource name in file system
            clean_name = res_name.replace('Resource', '')
            for f in os.listdir(ini_dir):
                if clean_name.lower() in f.lower() and f.lower().endswith(('.dds', '.png')):
                    fp = os.path.join(ini_dir, f)
                    if section not in section_textures:
                        section_textures[section] = []
                    section_textures[section].append(fp)
                    break
    
    # Also build component-based mapping (for WWMI format)
    comp_textures = {}
    for section, texs in section_tex_refs.items():
        for res_name in texs:
            fname = resource_files.get(res_name, '')
            if not fname:
                continue
            match = _re.search(r'Components-([0-9-]+)', fname)
            if match:
                for comp_str in match.group(1).split('-'):
                    try:
                        comp_num = int(comp_str)
                        if comp_num not in comp_textures:
                            comp_textures[comp_num] = []
                        res_data = _resolve_lookup(resolved, res_name)
                        if res_data and res_data.get('path'):
                            comp_textures[comp_num].append(res_data['path'])
                    except Exception:
                        pass
    
    # Merge: for sections with Component in name, use comp_textures
    for section in list(section_textures.keys()):
        match = _re.search(r'Component(\\d+)', section)
        if match:
            comp_num = int(match.group(1))
            if comp_num in comp_textures and comp_textures[comp_num]:
                section_textures[section] = comp_textures[comp_num]
    
    return section_textures


def parse_ini_full(ini_path):
    """Parse INI with section/IB/run resolution."""
    sections = {}
    resources = {}
    current_section = None
    current_condition = None

    with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('[') and s.endswith(']'):
            current_section = s[1:-1]
            if current_section not in sections:
                sections[current_section] = {'ib': None, 'vb0': None, 'vb1': None, 'vb2': None, 'draws': [], 'runs': [], 'textures': {}, 'hash': None, 'match_first_index': None, 'handling': None}
            continue
        if not current_section:
            continue

        # Track if/elif/else/endif for variant grouping
        if s.startswith('if '):
            current_condition = s[3:].strip()
        elif s.startswith('elif '):
            current_condition = s[5:].strip()
        elif s == 'else':
            current_condition = 'else'
        elif s == 'endif':
            current_condition = None

        if '=' not in s and not s.lstrip().startswith('drawindexed') and not s.lstrip().startswith('draw '):
            continue
        key, val = s.split('=', 1)
        key, val = key.strip(), val.strip()

        if current_section.startswith('Resource'):
            if current_section not in resources:
                resources[current_section] = {}
            if key == 'filename':
                resources[current_section]['filename'] = val
            elif key == 'stride':
                try: resources[current_section]['stride'] = int(val)
                except Exception: pass
            elif key == 'format':
                resources[current_section]['format'] = val
            elif key == 'type':
                resources[current_section]['type'] = val

        # Strip 'ref ' prefix (Endfield format: ib = ref Resource_...)
        def _strip_ref(v):
            return v[4:].strip() if v.lower().startswith('ref ') else v

        # Conditional: only take first assignment per slot (LOD0 before LOD1)
        in_conditional = current_condition is not None
        def _first_assign(slot):
            return sections[current_section].get(slot) is None

        if key == 'ib':
            if not in_conditional or _first_assign('ib'):
                sections[current_section]['ib'] = _strip_ref(val) if val.lower() != 'null' else None
        if key == 'vb0':
            if not in_conditional or _first_assign('vb0'):
                sections[current_section]['vb0'] = _strip_ref(val)
        if key == 'vb1':
            if not in_conditional or _first_assign('vb1'):
                sections[current_section]['vb1'] = _strip_ref(val)
        if key == 'vb2':
            if not in_conditional or _first_assign('vb2'):
                sections[current_section]['vb2'] = _strip_ref(val)
        if key == 'run':
            sections[current_section]['runs'].append(_strip_ref(val))
        if key == 'hash':
            sections[current_section]['hash'] = val.lower()
        if key == 'handling':
            sections[current_section]['handling'] = val.lower()
        if key == 'match_first_index':
            try: sections[current_section]['match_first_index'] = int(val)
            except Exception: pass

        # Capture texture references (multiple formats)
        if key.startswith('ps-t') and val.lower().startswith('resource'):
            slot = key.split('=')[0].strip()
            sections[current_section]['textures'][slot] = val
        elif key == 'this' and val.lower().startswith('resource'):
            sections[current_section]['textures']['this'] = val
        elif 'diffuse' in key.lower() and val.lower().startswith('ref '):
            ref_name = val[4:].strip()
            sections[current_section]['textures']['diffuse'] = ref_name
        elif 'normalmap' in key.lower() and val.lower().startswith('ref '):
            ref_name = val[4:].strip()
            sections[current_section]['textures']['normal'] = ref_name
        elif 'lightmap' in key.lower() and val.lower().startswith('ref '):
            ref_name = val[4:].strip()
            sections[current_section]['textures']['lightmap'] = ref_name

        # Draw calls: match drawindexed or draw, even with leading whitespace
        stripped = s.lstrip()
        if stripped.startswith('drawindexed') or stripped.startswith('draw '):
            parts = stripped.split('=', 1)
            if len(parts) == 2:
                args = [x.strip() for x in parts[1].split(',')]
                is_indexed = stripped.startswith('drawindexed')
                is_instanced = stripped.startswith('drawindexedinstanced')

                # Handle 'drawindexed = auto' (XXMI format)
                if is_indexed and args[0].lower() == 'auto':
                    name = current_section
                    for j in range(i - 1, max(i - 5, -1), -1):
                        cl = lines[j].strip()
                        if cl.startswith(';'):
                            c = cl[1:].strip()
                            if c and not c.startswith('=') and not c.startswith('draw'):
                                name = c
                                break
                    variant = current_condition if current_condition and current_condition != 'else' else None
                    sections[current_section]['draws'].append({
                        'name': name, 'section': current_section,
                        'type': 'drawindexed_auto',
                        'condition': variant,
                    })
                    continue

                # Safely parse integer args
                def safe_int(val):
                    try: return int(val)
                    except (ValueError, TypeError): return None

                if is_instanced:
                    # drawindexedinstanced = count, INSTANCE_COUNT, start, base, FIRST_INSTANCE
                    count = safe_int(args[0]) or 0
                    start = safe_int(args[2]) if len(args) > 2 else 0
                    base = safe_int(args[3]) if len(args) > 3 else 0
                else:
                    count = safe_int(args[0]) or 0
                    start = safe_int(args[1]) if len(args) > 1 else 0
                    base = safe_int(args[2]) if len(args) > 2 else 0

                if start is None: start = 0
                if base is None: base = 0

                name = current_section
                for j in range(i - 1, max(i - 5, -1), -1):
                    cl = lines[j].strip()
                    if cl.startswith(';'):
                        c = cl[1:].strip()
                        if c and not c.startswith('=') and not c.startswith('draw'):
                            name = c
                            break

                variant = current_condition if current_condition and current_condition != 'else' else None

                # Skip zero-count draw calls (placeholders)
                if is_indexed and count == 0:
                    continue

                if is_indexed:
                    sections[current_section]['draws'].append({
                        'name': name, 'section': current_section,
                        'index_count': count, 'start_index': start,
                        'base_vertex': base, 'type': 'drawindexed',
                        'condition': variant,
                    })
                else:
                    sections[current_section]['draws'].append({
                        'name': name, 'section': current_section,
                        'vertex_count': count, 'start_vertex': start,
                        'type': 'draw',
                        'condition': variant,
                    })

    # First pass: collect IB/VB from all sections (including command lists)
    section_res = {}  # section_name -> {ib, vb0, vb1, vb2}
    for sec_name, sec_data in sections.items():
        section_res[sec_name] = {
            'ib': sec_data['ib'],
            'vb0': sec_data['vb0'],
            'vb1': sec_data['vb1'],
            'vb2': sec_data.get('vb2'),
        }

    # Second pass: resolve run = chains (command list -> caller)
    caller_ctx = {}
    for sec_name, sec_data in sections.items():
        ctx = {
            'ib': sec_data['ib'],
            'vb0': sec_data['vb0'],
            'vb1': sec_data['vb1'],
            'vb2': sec_data.get('vb2'),
            'textures': sec_data['textures'].copy(),
        }
        for run_target in sec_data['runs']:
            if run_target in sections:
                # Inherit IB/VB from the command list
                cmd_res = section_res.get(run_target, {})
                if cmd_res.get('ib') and not ctx.get('ib'):
                    ctx['ib'] = cmd_res['ib']
                if cmd_res.get('vb0') and not ctx.get('vb0'):
                    ctx['vb0'] = cmd_res['vb0']
                if cmd_res.get('vb1') and not ctx.get('vb1'):
                    ctx['vb1'] = cmd_res['vb1']
                if cmd_res.get('vb2') and not ctx.get('vb2'):
                    ctx['vb2'] = cmd_res['vb2']
        # Store context for THIS section (not the target)
        caller_ctx[sec_name] = ctx

    # Build final draw calls
    draw_calls = []

    # Global VB collection: map hash -> {vb0, vb1, vb2}
    hash_vb_global = {}
    for sec_name, sec_data in sections.items():
        h = sec_data.get('hash')
        if not h: continue
        if h not in hash_vb_global:
            hash_vb_global[h] = {'vb0': None, 'vb1': None, 'vb2': None}
        ctx = caller_ctx.get(sec_name, {})
        v0 = sec_data['vb0'] or ctx.get('vb0')
        v1 = sec_data['vb1'] or ctx.get('vb1')
        v2 = sec_data.get('vb2') or ctx.get('vb2')
        if v0 and not hash_vb_global[h]['vb0']: hash_vb_global[h]['vb0'] = v0
        if v1 and not hash_vb_global[h]['vb1']: hash_vb_global[h]['vb1'] = v1
        if v2 and not hash_vb_global[h]['vb2']: hash_vb_global[h]['vb2'] = v2

    # Flow 1: Sections with explicit draw calls
    for sec_name, sec_data in sections.items():
        explicit_draws = [d for d in sec_data['draws'] if d['type'] in ('drawindexed', 'draw')]
        if not explicit_draws:
            continue

        ctx = caller_ctx.get(sec_name, {})
        eff_ib = sec_data['ib'] if sec_data['ib'] is not None else ctx.get('ib')
        eff_vb0 = sec_data['vb0'] if sec_data['vb0'] is not None else ctx.get('vb0')
        eff_vb1 = sec_data['vb1'] if sec_data['vb1'] is not None else ctx.get('vb1')
        eff_vb2 = sec_data.get('vb2') or ctx.get('vb2')
        eff_tex = {**ctx.get('textures', {}), **sec_data['textures']}

        # Try hash-based VB lookup
        h = sec_data.get('hash')
        if h and h in hash_vb_global:
            if not eff_vb0 and hash_vb_global[h]['vb0']: eff_vb0 = hash_vb_global[h]['vb0']
            if not eff_vb1 and hash_vb_global[h]['vb1']: eff_vb1 = hash_vb_global[h]['vb1']
            if not eff_vb2 and hash_vb_global[h]['vb2']: eff_vb2 = hash_vb_global[h]['vb2']

        for dc in explicit_draws:
            dc['ib_resource'] = eff_ib
            dc['vb0_resource'] = eff_vb0
            dc['vb1_resource'] = eff_vb1
            dc['vb2_resource'] = eff_vb2
            dc['textures'] = eff_tex.copy()
            if 'condition' not in dc:
                dc['condition'] = None
            draw_calls.append(dc)

    # Flow 2: match_first_index sections (XXMI/ZZMI/WWMI/EFMI)
    hash_groups = {}
    for sec_name, sec_data in sections.items():
        h = sec_data.get('hash')
        if h:
            if h not in hash_groups: hash_groups[h] = []
            hash_groups[h].append(sec_name)

    for hash_val, sec_names in hash_groups.items():
        auto_sections = []
        for sn in sec_names:
            sd = sections[sn]
            if any(d['type'] in ('drawindexed', 'draw') for d in sd['draws']):
                continue
            if sd.get('handling') == 'skip':
                continue
            if sd.get('match_first_index') is not None and sd.get('ib'):
                auto_sections.append(sn)

        if not auto_sections:
            continue

        # Get VB for this hash group
        hb = hash_vb_global.get(hash_val, {})
        vb0 = hb.get('vb0')
        vb1 = hb.get('vb1')
        vb2 = hb.get('vb2')
        if not vb0:
            for h2, hb2 in hash_vb_global.items():
                if hb2.get('vb0'): vb0 = hb2['vb0']; break
        if not vb1:
            for h2, hb2 in hash_vb_global.items():
                if hb2.get('vb1'): vb1 = hb2['vb1']; break
        if not vb2:
            for h2, hb2 in hash_vb_global.items():
                if hb2.get('vb2'): vb2 = hb2['vb2']; break

        # Collect textures
        hash_tex = {}
        for sn in sec_names:
            sd = sections[sn]
            ctx = caller_ctx.get(sn, {})
            hash_tex.update(ctx.get('textures', {}))
            hash_tex.update(sd['textures'])

        auto_sections.sort(key=lambda sn: sections[sn]['match_first_index'])

        for sn in auto_sections:
            sd = sections[sn]
            # Get index count from IB file size
            index_count = 0
            ib_res_name = sd['ib']
            if ib_res_name in resources and 'filename' in resources[ib_res_name]:
                ib_path = os.path.join(os.path.dirname(ini_path), resources[ib_res_name]['filename'])
                if os.path.exists(ib_path):
                    index_count = os.path.getsize(ib_path) // 4
            if index_count <= 0:
                continue

            dc = {
                'name': sn, 'section': sn, 'type': 'drawindexed',
                'index_count': index_count, 'start_index': 0, 'base_vertex': 0,
                'ib_resource': sd['ib'],
                'vb0_resource': vb0, 'vb1_resource': vb1, 'vb2_resource': vb2,
                'textures': sd['textures'].copy(),
                'condition': None,
            }
            draw_calls.append(dc)

    return draw_calls, resources


# ============================================================
# Buffer Resolution
# ============================================================

def resolve_resources(ini_dir, resources):
    """Resolve all resource files by full relative path, with fallback to filename search."""
    all_files_rel = {}
    all_files_name = {}
    for root, dirs, files in os.walk(ini_dir):
        # Limit scan depth to 3 levels for performance
        depth = root.replace(ini_dir, '').count(os.sep)
        if depth > 3:
            dirs.clear()
            continue
        for f in files:
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, ini_dir).replace('\\', '/')
            all_files_rel[rel.lower()] = fp
            all_files_rel[rel] = fp
            if f not in all_files_name:
                all_files_name[f] = fp
    
    print(f"  [Migoto] resolve_resources: {len(set(all_files_rel.values()))} files in {ini_dir}")
    # Show all .buf files found
    for f in sorted(all_files_name.keys()):
        if f.endswith('.buf'):
            print(f"    Found: {f}")
    
    resolved = {}
    for rname, rdata in resources.items():
        if 'filename' not in rdata:
            continue
        
        fname = rdata['filename'].replace('\\', '/')
        fp = None
        
        candidate = os.path.join(ini_dir, fname)
        if os.path.exists(candidate):
            fp = candidate
        elif fname in all_files_rel:
            fp = all_files_rel[fname]
        elif fname.lower() in all_files_rel:
            fp = all_files_rel[fname.lower()]
        elif os.path.basename(fname) in all_files_name:
            fp = all_files_name[os.path.basename(fname)]
        else:
            basename_lower = os.path.basename(fname).lower()
            for af_name, af_path in all_files_name.items():
                if af_name.lower() == basename_lower:
                    fp = af_path
                    break
        
        if fp and os.path.exists(fp):
            resolved[rname] = {
                'path': fp,
                'stride': rdata.get('stride', 0),
                'format': rdata.get('format', ''),
                'type': rdata.get('type', ''),
            }
        else:
            # Debug: show what we tried
            if 'Buffer' in rname or 'Index' in rname:
                print(f"    NOT FOUND: {rname} -> {fname}")
                print(f"      Tried: {candidate}")
                # Suggest possible causes
                basename = os.path.basename(fname)
                similar = [f for f in all_files_name if basename.lower() in f.lower() or f.lower() in basename.lower()]
                if similar:
                    print(f"      Similar files found: {similar[:3]}")
                elif not os.path.isdir(ini_dir):
                    print(f"      Directory does not exist: {ini_dir}")
    
    return resolved


def load_mesh_data(resolved, draw_calls, mirror_x=False, mirror_y=False, mirror_z=False, rot_x=0, rot_y=0, rot_z=0, uv_format="auto", game_format="auto"):
    """Load vertex/UV/index data per IB."""
    # Find unique IBs and their associated VB resources
    ib_set = set()
    ib_vb_map = {}  # ib_name -> {vb0, vb1, vb2} from draw calls
    for dc in draw_calls:
        ib = dc.get('ib_resource')
        if ib:
            ib_set.add(ib)
            if ib not in ib_vb_map:
                ib_vb_map[ib] = {
                    'vb0': dc.get('vb0_resource'),
                    'vb1': dc.get('vb1_resource'),
                    'vb2': dc.get('vb2_resource'),
                }

    mesh_data = {}

    for ib_name in ib_set:
        if not _resolve_lookup(resolved, ib_name):
            continue

        indices = read_indices(resolved[ib_name]['path'], resolved[ib_name].get('format', ''))

        # Try explicit VB resources from draw calls first (WWMI format)
        vb_info = ib_vb_map.get(ib_name, {})
        pos_res = vb_info.get('vb0')
        uv_res = vb_info.get('vb2') or vb_info.get('vb1')  # WWMI uses vb2 for texcoord

        # Verify UV resource is actually a texcoord buffer, not a blend buffer
        # (In XXMI format, both Blend and Texcoord can be on vb1 with different hashes)
        if uv_res and _resolve_lookup(resolved, uv_res):
            r_check = _resolve_lookup(resolved, uv_res)
            stride = r_check.get('stride', 0)
            name_lower = (uv_res or '').lower()
            is_blend = ('blend' in name_lower or 'weight' in name_lower or
                       stride in (32, 48, 64, 96, 128))
            if is_blend:
                # Search for actual texcoord resource with same component prefix
                ib_clean = ib_name.replace('Resource', '').replace('_', '').lower()
                for rname in resolved:
                    rl = rname.lower()
                    if 'texcoord' in rl or 'texcoord' in rl:
                        # Check if same component (e.g. both "Head")
                        r_clean = rname.replace('Resource', '').replace('Texcoord', '').replace('_', '').lower()
                        if ib_clean.startswith(r_clean) or r_clean.startswith(ib_clean.replace('ib', '').replace('aib', '')):
                            uv_res = rname
                            break
                # Fallback: just find any texcoord resource
                if is_blend:
                    for rname in resolved:
                        if 'texcoord' in rname.lower():
                            uv_res = rname
                            break

        # Fall back to hash/prefix matching (3Dmigoto format)
        if not pos_res or pos_res not in resolved:
            ib_clean = ib_name.replace('Resource', '').replace('_', '')
            import re as _re
            hash_match = _re.search(r'[0-9a-f]{8}', ib_clean.lower())
            if hash_match:
                ib_hash = hash_match.group()
            else:
                ib_hash = ib_clean
                for suffix in ['AIB', 'BIB', 'IB', 'Component1', 'Component2', 'Component3', 'CS']:
                    ib_hash = ib_hash.replace(suffix, '')
                ib_hash = ib_hash.lower()

            pos_res = None
            uv_res = None
            mesh_parts = ['head', 'body', 'dress', 'hair', 'face', 'arm', 'leg', 'foot', 'hand', 'tail', 'hat', 'skirt', 'heel']
            
            # Try matching with progressively shorter prefixes
            search_key = ib_hash
            for _ in range(3):
                for rname in resolved:
                    rlower = rname.lower().replace('_', '')
                    if 'position' in rlower or 'texcoord' in rlower:
                        # Extract the component prefix from resource name
                        r_clean = rname.replace('Resource', '').replace('Position', '').replace('Texcoord', '').replace('CS', '').replace('_', '').lower()
                        # Check if IB prefix matches resource prefix
                        if search_key and r_clean.startswith(search_key):
                            if 'position' in rlower:
                                pos_res = rname
                            else:
                                uv_res = rname
                if pos_res and uv_res:
                    break
                # Strip mesh part and retry
                for part in sorted(mesh_parts, key=len, reverse=True):
                    if search_key.endswith(part) and len(search_key) > len(part):
                        search_key = search_key[:-len(part)]
                        break
                else:
                    break

        positions = []
        if pos_res and _resolve_lookup(resolved, pos_res):
            r = _resolve_lookup(resolved, pos_res)
            positions = read_positions(r['path'], r['stride'], mirror_x=mirror_x, mirror_y=mirror_y, mirror_z=mirror_z, rot_x=rot_x, rot_y=rot_y, rot_z=rot_z)

        uvs = []
        if uv_res and _resolve_lookup(resolved, uv_res):
            r = _resolve_lookup(resolved, uv_res)
            # Game-specific UV format override
            effective_uv_fmt = uv_format
            if game_format == 'ef':
                # Endfield: UV is in VB1 bytes 0-7 as float32 x2
                vb1_r = _resolve_lookup(resolved, vb_info.get('vb1'))
                if vb1_r and vb1_r['stride'] == 12:
                    effective_uv_fmt = 'f32_0'
                    uv_res = vb_info.get('vb1')
                    r = vb1_r
            if game_format == 'ww':
                # Wuthering Waves: UV is in TexCoord buffer, stride 16, half-float @0
                if r and r['stride'] == 16:
                    effective_uv_fmt = 'hf0'
            uvs = read_uvs(r['path'], r['stride'], uv_format=effective_uv_fmt)

        # Read blend weights
        blend_res = vb_info.get('vb2') or vb_info.get('vb1')
        # Verify it's actually a blend buffer
        if blend_res and _resolve_lookup(resolved, blend_res):
            br = _resolve_lookup(resolved, blend_res)
            blend_name_lower = (blend_res or '').lower()
            if 'texcoord' in blend_name_lower or 'position' in blend_name_lower:
                blend_res = None
        # For EFMI: blend is vb1, but vb1 might also be texcoord
        if game_format == 'ef' and blend_res:
            br = _resolve_lookup(resolved, blend_res)
            if br and br.get('stride', 0) not in (8, 12, 16, 32, 48, 64):
                blend_res = None

        # Fallback: find blend buffer by name matching (XXMI/ZZZ format)
        # IB name like 'Resource625c2692Blend' -> find matching 'Resource625c2692Blend'
        if not blend_res or not _resolve_lookup(resolved, blend_res):
            ib_clean = ib_name.replace('Resource', '').replace('IB', '').replace('AIB', '').replace('BIB', '').replace('_', '').lower()
            for rname in resolved:
                rl = rname.lower()
                if 'blend' in rl:
                    # Check if same component prefix
                    r_clean = rname.replace('Resource', '').replace('Blend', '').replace('CS', '').replace('_', '').lower()
                    if ib_clean.startswith(r_clean) or r_clean.startswith(ib_clean):
                        blend_res = rname
                        break
            # Generic fallback: any blend buffer
            if not blend_res or not _resolve_lookup(resolved, blend_res):
                for rname in resolved:
                    if 'blend' in rname.lower() and 'cs' not in rname.lower():
                        blend_res = rname
                        break

        blend_weights = []
        if blend_res and _resolve_lookup(resolved, blend_res):
            r = _resolve_lookup(resolved, blend_res)
            blend_weights = read_blend_weights(r['path'], r['stride'])
            if blend_weights:
                print(f"  [Migoto] Blend: {blend_res} ({len(blend_weights)} verts, stride={r['stride']})")

        mesh_data[ib_name] = {
            'positions': positions,
            'uvs': uvs,
            'indices': indices,
            'blend_weights': blend_weights,
        }

        print(f"  IB: {ib_name} -> pos={pos_res} ({len(positions)} verts), uv={uv_res} ({len(uvs)} uvs), idx={len(indices)}")

    return mesh_data


# Texture type classification keywords
TEX_TYPE_KEYWORDS = {
    'diffuse': ['diffuse', 'basecolor', 'base_color', 'albedo', 'color'],
    'lightmap': ['lightmap', 'light_map', 'specular', 'spec', 'roughness'],
    'normal': ['normal', 'normalmap', 'normal_map'],
    'shadow': ['shadow', 'ao', 'ambient_occlusion'],
    'fx': ['fx', 'emission', 'emissive', 'glow', 'stockingmap', 'stocking', 'materialmap'],
    'alpha': ['alpha', 'transparency', 'opacity', 'mask'],
}


def classify_texture(filepath_or_name):
    """Classify texture type from filename or resource name."""
    name_lower = os.path.basename(filepath_or_name).lower()
    for tex_type, keywords in TEX_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return tex_type
    return 'unknown'


def find_all_textures(ini_dir, resources, resolved, search_dirs=None):
    """Find ALL textures from resources, classified by type.
    Returns dict: {resource_name: {'path': ..., 'type': ..., 'slot': ...}}
    """
    if search_dirs is None:
        search_dirs = [ini_dir]

    all_tex = {}

    # From resolved resources
    for rname, rdata in resources.items():
        fname = rdata.get('filename', '')
        if not fname:
            continue
        fl = fname.lower()
        if not any(ext in fl for ext in ['.dds', '.png', '.jpg', '.tga']):
            continue

        # Get resolved path
        fp = None
        if rname in resolved:
            fp = resolved[rname]['path']
        elif os.path.exists(os.path.join(ini_dir, fname)):
            fp = os.path.join(ini_dir, fname)
        if not fp or not os.path.exists(fp):
            continue

        tex_type = classify_texture(fname)
        all_tex[rname] = {'path': fp, 'type': tex_type, 'filename': fname}

    return all_tex


def assign_textures_to_material(mat, tex_assignments, name_prefix=''):
    """Assign multiple texture types to a material's Principled BSDF.
    tex_assignments: dict of {tex_type: filepath}
    """
    if not mat or not mat.use_nodes:
        return
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Find BSDF node
    bsdf = None
    for n in nodes:
        if n.type == 'BSDF_PRINCIPLED':
            bsdf = n
            break
    if not bsdf:
        return

    # Track node positions for layout
    y_offset = -300

    for tex_type, filepath in tex_assignments.items():
        if not filepath or not os.path.exists(filepath):
            continue

        # Load image
        img_name = f"{name_prefix}_{tex_type}"
        img = load_dds(filepath, img_name)
        if not img:
            continue

        # Set color space
        if tex_type in ('diffuse', 'fx'):
            img.colorspace_settings.name = 'sRGB'
        else:
            img.colorspace_settings.name = 'Non-Color'

        # Create texture node
        tex_node = nodes.new('ShaderNodeTexImage')
        tex_node.location = (-600, y_offset)
        tex_node.image = img
        tex_node.label = f"{tex_type}: {os.path.basename(filepath)}"

        # Connect to appropriate BSDF input
        if tex_type == 'diffuse':
            links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
        elif tex_type == 'normal':
            # Normal map needs a Normal Map node
            nm_node = nodes.new('ShaderNodeNormalMap')
            nm_node.location = (-300, y_offset)
            links.new(tex_node.outputs['Color'], nm_node.inputs['Color'])
            links.new(nm_node.outputs['Normal'], bsdf.inputs['Normal'])
        elif tex_type == 'lightmap':
            # LightMap often used as specular/roughness
            # Try connecting to Specular or Roughness
            if 'Specular IOR Level' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Specular IOR Level'])
            elif 'Specular' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Specular'])
        elif tex_type == 'shadow':
            # Shadow/AO map - mix with diffuse
            pass  # Usually baked into diffuse
        elif tex_type == 'fx':
            # Emission/FX
            if 'Emission Color' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Emission Color'])
            elif 'Emission' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Emission'])
        elif tex_type == 'alpha':
            links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
            mat.blend_method = 'CLIP'

        y_offset -= 250


def find_texture_slots_from_ini(ini_path, draw_calls):
    """Parse INI to find texture slot assignments per draw call section.
    Returns: {section_name: {'ps-t0': resource_name, 'ps-t1': resource_name, ...}}
    """
    section_slots = {}
    current_section = None

    with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            s = line.strip()
            if s.startswith('[') and s.endswith(']'):
                current_section = s[1:-1]
                continue
            if not current_section or '=' not in s:
                continue
            key, val = s.split('=', 1)
            key, val = key.strip(), val.strip()

            # Capture ps-t* and this = Resource references
            if key.startswith('ps-t') and val.startswith('Resource'):
                if current_section not in section_slots:
                    section_slots[current_section] = {}
                section_slots[current_section][key] = val
            elif key == 'this' and val.startswith('Resource'):
                if current_section not in section_slots:
                    section_slots[current_section] = {}
                section_slots[current_section]['this'] = val

    return section_slots


def build_texture_assignment_map(ini_path, ini_dir, resources, resolved, draw_calls):
    """Build texture assignments per draw call.
    Returns: {section_name: {'diffuse': path, 'lightmap': path, 'normal': path, ...}}
    """
    section_slots = find_texture_slots_from_ini(ini_path, draw_calls)
    all_tex = find_all_textures(ini_dir, resources, resolved)

    # Build resource name -> path mapping
    res_to_path = {}
    for rname, rdata in all_tex.items():
        res_to_path[rname] = rdata['path']
        # Also add without 'Resource' prefix
        clean = rname.replace('Resource', '').lower()
        res_to_path[clean] = rdata['path']

    result = {}

    for dc in draw_calls:
        sec = dc.get('section', '')
        if sec in result:
            continue

        tex_assign = {}
        slots = section_slots.get(sec, {})

        # Also check draw call's own textures dict
        dc_texs = dc.get('textures', {})
        all_slots = {**slots, **dc_texs}

        for slot_key, res_name in all_slots.items():
            if not res_name:
                continue

            # Resolve resource to file path
            fp = None
            if res_name in resolved:
                fp = resolved[res_name].get('path')
            elif res_name in res_to_path:
                fp = res_to_path[res_name]

            if not fp or not os.path.exists(fp):
                continue

            # Classify by resource name or filename
            tex_type = classify_texture(res_name)
            if tex_type == 'unknown':
                tex_type = classify_texture(fp)

            if tex_type != 'unknown' and tex_type not in tex_assign:
                tex_assign[tex_type] = fp

        if tex_assign:
            result[sec] = tex_assign

    return result


def check_dds_format(filepath):
    """Check DDS format. Returns DX10 format code or None."""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(132)
        if len(header) < 132 or header[:4] != b'DDS ':
            return None
        fourcc = header[84:88]
        if fourcc == b'DX10':
            return struct.unpack_from('<I', header, 128)[0]
        return None
    except Exception:
        return None


def load_dds(filepath, name):
    """Load texture into Blender. Handles BC7 DDS conversion."""
    if not filepath:
        return None
    if not os.path.exists(filepath):
        return None
    if name in bpy.data.images:
        return bpy.data.images[name]
    
    # For non-DDS files, load directly
    if not filepath.lower().endswith('.dds'):
        try:
            img = bpy.data.images.load(filepath)
            img.name = name
            return img
        except Exception:
            return None
    
    # Check DDS format - BC7 DDS files often have alpha=0 (mask textures)
    # Blender reads the alpha channel and makes the mesh transparent
    # Solution: convert BC7 DDS via PNG with alpha forced to 255
    dds_fmt = check_dds_format(filepath)
    is_diffuse = 'Diffuse' in name or 'diffuse' in name or 'Base' in name
    if dds_fmt in (98, 99, 95, 83, 84):  # BC7_UNORM, BC7_SRGB, BC6H, BC5
        # Convert via Pillow with alpha fix
        try:
            from PIL import Image as PILImage
            import tempfile
            temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
            os.makedirs(temp_dir, exist_ok=True)
            png_name = os.path.splitext(os.path.basename(filepath))[0] + '.png'
            png_path = os.path.join(temp_dir, png_name)
            if not os.path.exists(png_path):
                pil_img = PILImage.open(filepath)
                if pil_img.mode == 'RGBA':
                    r, g, b, a = pil_img.split()
                    a_data = list(a.getdata())
                    zero_ratio = sum(1 for v in a_data if v < 10) / len(a_data)
                    if zero_ratio > 0.3:  # More than 30% alpha near zero
                        a = PILImage.new('L', pil_img.size, 255)
                        pil_img = PILImage.merge('RGBA', (r, g, b, a))
                        print(f"  [Migoto] Fixed alpha ({zero_ratio:.0%} was zero): {os.path.basename(filepath)}")
                elif pil_img.mode == 'RGB':
                    pil_img = pil_img.convert('RGBA')
                pil_img.save(png_path)
            img = bpy.data.images.load(png_path)
            img.name = name
            img.colorspace_settings.name = 'sRGB' if is_diffuse else 'Non-Color'
            return img
        except Exception as e:
            print(f"  [Migoto] BC7 conversion failed: {os.path.basename(filepath)}: {e}")
    
    # For other DDS formats: try Blender's native DDS loader
    try:
        img = bpy.data.images.load(filepath)
        img.name = name
        img.colorspace_settings.name = 'sRGB' if is_diffuse else 'Non-Color'
        if img.size[0] == 0 or img.size[1] == 0:
            bpy.data.images.remove(img)
            raise Exception('Empty image')
        return img
    except Exception as e:
        print(f"  [Migoto] Blender DDS load failed: {os.path.basename(filepath)}: {e}")
    
    # Fallback: convert DDS to PNG via Pillow
    try:
        from PIL import Image as PILImage
        import tempfile
        temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
        os.makedirs(temp_dir, exist_ok=True)
        png_name = os.path.splitext(os.path.basename(filepath))[0] + '.png'
        png_path = os.path.join(temp_dir, png_name)
        if not os.path.exists(png_path):
            PILImage.open(filepath).save(png_path)
        img = bpy.data.images.load(png_path)
        img.name = name
        return img
    except Exception:
        return None


# ============================================================
# Material & Mesh
# ============================================================

def make_material(name, diffuse=None, alpha=False, tex_assignments=None):
    """Create a Principled BSDF material with texture support.
    tex_assignments: dict of {tex_type: filepath} for multi-texture support.
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for n in nodes:
        nodes.remove(n)
    out = nodes.new('ShaderNodeOutputMaterial')
    out.location = (400, 0)
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (200, 0)
    links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    
    # If we have full texture assignments, use them
    if tex_assignments:
        assign_textures_to_material(mat, tex_assignments, name_prefix=name)
        return mat
    
    # Fallback: always create texture node and connect, only load image if file exists
    t = nodes.new('ShaderNodeTexImage')
    t.location = (-300, 0)
    links.new(t.outputs['Color'], bsdf.inputs['Base Color'])
    if diffuse:
        img = load_dds(diffuse, f"{name}_Diffuse")
        if img:
            t.image = img
            if alpha:
                links.new(t.outputs['Alpha'], bsdf.inputs['Alpha'])
                mat.blend_method = 'CLIP'
    
    return mat



def safe_name(name):
    return re.sub(r'[^\w\-. ]', '_', name).strip('_')


def build_object(name, all_positions, all_uvs, triangles, material, collection, mirror_faces=False, blend_weights=None):
    """Build Blender mesh from triangles. Only creates referenced vertices."""
    used = set()
    for i0, i1, i2 in triangles:
        used.update([i0, i1, i2])
    max_idx = len(all_positions) - 1
    used = {i for i in used if 0 <= i <= max_idx}
    if not used:
        return None, 0

    sorted_idx = sorted(used)
    remap = {old: new for new, old in enumerate(sorted_idx)}

    verts = [all_positions[i] for i in sorted_idx]
    vert_uvs = [all_uvs[i] if i < len(all_uvs) else (0.0, 0.0) for i in sorted_idx]
    vert_blend = [blend_weights[i] if blend_weights and i < len(blend_weights) else [] for i in sorted_idx]

    faces = []
    for i0, i1, i2 in triangles:
        if i0 in remap and i1 in remap and i2 in remap:
            if mirror_faces:
                # Reverse winding to fix normals after mirror
                faces.append((remap[i0], remap[i2], remap[i1]))
            else:
                faces.append((remap[i0], remap[i1], remap[i2]))
    if not faces:
        return None, 0

    # Direct mesh API (more reliable UV handling than bmesh)
    mesh = bpy.data.meshes.new(safe_name(name))
    obj = bpy.data.objects.new(safe_name(name), mesh)
    collection.objects.link(obj)

    mesh.vertices.add(len(verts))
    mesh.vertices.foreach_set('co', [c for v in verts for c in v])

    mesh.loops.add(len(faces) * 3)
    mesh.loops.foreach_set('vertex_index', [vi for f in faces for vi in f])

    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set('loop_start', list(range(0, len(faces) * 3, 3)))
    mesh.polygons.foreach_set('loop_total', [3] * len(faces))

    uv_layer = mesh.uv_layers.new(name='UVMap')
    uv_array = [0.0] * (len(faces) * 6)
    for face_idx, (i0, i1, i2) in enumerate(faces):
        base = face_idx * 6
        u0, v0 = vert_uvs[i0]
        u1, v1 = vert_uvs[i1]
        u2, v2 = vert_uvs[i2]
        uv_array[base] = u0; uv_array[base + 1] = v0
        uv_array[base + 2] = u1; uv_array[base + 3] = v1
        uv_array[base + 4] = u2; uv_array[base + 5] = v2
    uv_layer.data.foreach_set('uv', uv_array)

    mesh.update()

    # Create vertex groups from blend weights
    if any(vert_blend):
        # Collect all unique bone indices
        all_bones = set()
        for vw in vert_blend:
            for bone_idx, weight in vw:
                all_bones.add(bone_idx)

        # Create a vertex group for each bone
        bone_groups = {}
        for bone_idx in sorted(all_bones):
            vg = obj.vertex_groups.new(name=f'Bone_{bone_idx}')
            bone_groups[bone_idx] = vg

        # Assign weights
        for vi, vw in enumerate(vert_blend):
            for bone_idx, weight in vw:
                if bone_idx in bone_groups and weight > 0.001:
                    bone_groups[bone_idx].add([vi], weight, 'REPLACE')

    if material:
        obj.data.materials.append(material)
    for p in obj.data.polygons:
        p.use_smooth = True

    return obj, len(faces)


# ============================================================
# Import Operator
# ============================================================

# Temporary file cleanup
def cleanup_migoto_temp():
    """Delete temp PNG files that are NOT currently loaded in Blender."""
    import tempfile
    temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
    if not os.path.isdir(temp_dir):
        return
    # Collect filepaths of images currently loaded in Blender
    active_files = set()
    for img in bpy.data.images:
        if img.filepath:
            try:
                active_files.add(os.path.normpath(bpy.path.abspath(img.filepath)))
            except Exception:
                pass
    try:
        for f in os.listdir(temp_dir):
            fp = os.path.join(temp_dir, f)
            if f.endswith('.png') and os.path.isfile(fp):
                # Skip files still loaded in Blender
                if os.path.normpath(fp) in active_files:
                    continue
                try:
                    os.remove(fp)
                except Exception:
                    pass
    except Exception:
        pass


class IMPORT_OT_3dmigoto(bpy.types.Operator, ImportHelper):
    """Import 3Dmigoto/XXMI model"""
    bl_idname = "import_scene.migoto_model"
    bl_label = "导入 3Dmigoto 模型 / Import 3Dmigoto Model"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".ini;.zip;.rar;.7z"
    filter_glob: StringProperty(default="*.ini;*.zip;*.rar;*.7z", options={'HIDDEN'})
    mirror_x: BoolProperty(name="镜像 X 轴 / Mirror X", default=False)
    mirror_y: BoolProperty(name="镜像 Y 轴 / Mirror Y", default=False)
    mirror_z: BoolProperty(name="镜像 Z 轴 / Mirror Z", default=False)
    rot_x: bpy.props.FloatProperty(name="X 旋转 / Rotate X", default=0, min=-360, max=360, step=10, subtype='ANGLE', unit='ROTATION')
    rot_y: bpy.props.FloatProperty(name="Y 旋转 / Rotate Y", default=0, min=-360, max=360, step=10, subtype='ANGLE', unit='ROTATION')
    rot_z: bpy.props.FloatProperty(name="Z 旋转 / Rotate Z", default=0, min=-360, max=360, step=10, subtype='ANGLE', unit='ROTATION')
    split_parts: BoolProperty(name="分离部件 / Split Into Parts", default=True)
    load_textures: BoolProperty(name="加载贴图 / Load Textures", default=True)
    load_special_textures: BoolProperty(name="启用特殊贴图 / Enable Special Textures", default=False, description="导入LightMap、Normal、FX等特殊贴图（Diffuse始终加载）")
    game_format: bpy.props.EnumProperty(
        name="游戏 / Game",
        items=[
            ('auto', '自动检测 / Auto', '根据INI自动检测'),
            ('sr', '崩铁 / Star Rail', '崩坏：星穹铁道'),
            ('zzz', '绝区零 / ZZZ', '绝区零'),
            ('gi', '原神 / Genshin', '原神'),
            ('ww', '鸣潮 / Wuthering Waves', '鸣潮'),
            ('ef', '终末地 / Endfield', '明日方舟：终末地'),
        ],
        default='auto',
    )
    uv_format: bpy.props.EnumProperty(
        name="UV 格式 / UV Format",
        items=[
            ('auto', '自动检测 / Auto', '自动检测UV格式（根据游戏选择会更准确）'),
            ('hf0', 'Half-float @0', ''),
            ('hf4', 'Half-float @4', ''),
            ('u16_0', 'uint16 @0', ''),
            ('u16_2', 'uint16 @2', ''),
            ('u16_4', 'uint16 @4', ''),
            ('f32_4', 'float32 @4', ''),
            ('f32_0', 'float32 @0', ''),
            ('u16_split', '终末地 VB2 / Endfield VB2', 'U@0 V@4 非连续uint16'),
            ('ef_vb2', '终末地 VB2 @0 / Endfield VB2', 'uint16 @0 (VB2前4字节，跳过前9顶点)'),
        ],
        default='auto',
    )

    def execute(self, context):
        try:
            return self._run(context)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}

    def _run(self, context):
        # Clean up old temp PNG files from previous imports
        cleanup_migoto_temp()

        ini_path = self.filepath
        
        # If a directory is selected, find .ini files inside
        if os.path.isdir(ini_path):
            ini_found = None
            for root, dirs, files in os.walk(ini_path):
                for f in files:
                    if f.lower().endswith('.ini'):
                        ini_found = os.path.join(root, f)
                        break
                if ini_found:
                    break
            if not ini_found:
                self.report({'ERROR'}, f'No .ini file found in directory: {ini_path}')
                return {'CANCELLED'}
            ini_path = ini_found
            print(f"  [Migoto] Found INI in directory: {ini_path}")
        
        # Handle archive files (ZIP/RAR/7z)
        if ini_path.lower().endswith(('.zip', '.rar', '.7z')):
            import tempfile
            extract_dir = tempfile.mkdtemp(prefix='migoto_')
            print(f"  [Migoto] Extracting archive to: {extract_dir}")
            
            if ini_path.lower().endswith('.zip'):
                import zipfile
                with zipfile.ZipFile(ini_path, 'r') as zf:
                    zf.extractall(extract_dir)
            elif ini_path.lower().endswith('.rar'):
                try:
                    import rarfile
                    with rarfile.RarFile(ini_path, 'r') as rf:
                        rf.extractall(extract_dir)
                except Exception as e:
                    self.report({'ERROR'}, f'Cannot extract RAR: {e}. Install unrar or extract manually.')
                    return {'CANCELLED'}
            elif ini_path.lower().endswith('.7z'):
                try:
                    import py7zr
                    with py7zr.SevenZipFile(ini_path, 'r') as sz:
                        sz.extractall(extract_dir)
                except Exception as e:
                    self.report({'ERROR'}, f'Cannot extract 7z: {e}. Install py7zr or extract manually.')
                    return {'CANCELLED'}
            
            # Find INI file in extracted content
            ini_found = None
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f.lower().endswith('.ini'):
                        ini_found = os.path.join(root, f)
                        break
                if ini_found:
                    break
            
            if not ini_found:
                self.report({'ERROR'}, 'No .ini file found in archive')
                return {'CANCELLED'}
            
            ini_path = ini_found
            print(f"  [Migoto] Found INI: {ini_path}")
        
        ini_dir = os.path.dirname(ini_path)

        # Find mod root: INI might be in a subdirectory (e.g. resources/)
        # Search parent dirs too for texture resolution
        search_dirs = [ini_dir]
        parent = os.path.dirname(ini_dir)
        if parent and parent != ini_dir:
            search_dirs.append(parent)
        grandparent = os.path.dirname(parent)
        if grandparent and grandparent != parent:
            search_dirs.append(grandparent)

        # Validate INI file
        if not os.path.isfile(ini_path):
            self.report({'ERROR'}, f'INI file not found: {ini_path}')
            return {'CANCELLED'}
        try:
            with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
                f.read(1024)  # Test readability
        except Exception as e:
            self.report({'ERROR'}, f'Cannot read INI file: {e}')
            return {'CANCELLED'}

        draw_calls, resources = parse_ini_full(ini_path)
        if not draw_calls:
            self.report({'ERROR'}, "No draw calls found in INI file")
            return {'CANCELLED'}

        # Resolve resources: try INI dir first, then parent dirs
        resolved = None
        for sdir in search_dirs:
            resolved = resolve_resources(sdir, resources)
            if resolved:
                break
        if not resolved:
            resolved = {}

        print(f"  [Migoto] Resources resolved: {len(resolved)}, Draw calls: {len(draw_calls)}")
        # Debug: show IB/VB buffer resources
        ib_names = set(dc.get('ib_resource') for dc in draw_calls if dc.get('ib_resource'))
        vb0_names = set(dc.get('vb0_resource') for dc in draw_calls if dc.get('vb0_resource'))
        vb2_names = set(dc.get('vb2_resource') for dc in draw_calls if dc.get('vb2_resource'))
        for name in ib_names | vb0_names | vb2_names:
            if name in resolved:
                print(f"    {name} -> {resolved[name]['path']}")
            else:
                print(f"    {name} -> NOT FOUND")

        # Determine game format
        game_fmt = self.game_format
        if game_fmt == 'auto':
            # Auto-detect from INI content
            ini_content = ''
            try:
                with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
                    ini_content = f.read().lower()
            except Exception: pass
            if 'commandlist\\efmiv1\\' in ini_content or 'commandlist/efmiv1/' in ini_content:
                game_fmt = 'ef'
            elif 'commandlist\\wwmiv1\\' in ini_content or 'commandlist/wwmiv1/' in ini_content:
                game_fmt = 'ww'
            elif 'commandlist\\xhmiv1\\' in ini_content or 'commandlist/xhmiv1/' in ini_content:
                game_fmt = 'sr'
            elif 'commandlist\\zzmiv1\\' in ini_content or 'commandlist/zzmiv1/' in ini_content:
                game_fmt = 'zzz'
            elif 'resource\\srmi\\' in ini_content or 'resource/srmi/' in ini_content:
                game_fmt = 'sr'
            elif 'resourcepositionbuffer' in ini_content and 'resourcetexcoordbuffer' in ini_content:
                game_fmt = 'ww'
            elif 'component0_vb0' in ini_content and 'meshes/' in ini_content:
                game_fmt = 'ef'
            print(f"  [Migoto] Game format: {game_fmt}")

        # Load mesh data per IB
        mesh_data = load_mesh_data(resolved, draw_calls, mirror_x=self.mirror_x, mirror_y=self.mirror_y, mirror_z=self.mirror_z, rot_x=self.rot_x, rot_y=self.rot_y, rot_z=self.rot_z, uv_format=self.uv_format, game_format=game_fmt)

        print(f"  [Migoto] Mesh data loaded: {len(mesh_data)} IBs")

        # Build component texture map from INI
        comp_textures = build_component_texture_map(ini_path, ini_dir, resolved)
        if comp_textures:
            print(f"  [Migoto] Component texture map: {len(comp_textures)} components")
            for comp, texs in sorted(comp_textures.items()):
                print(f"    Component {comp}: {len(texs)} textures")

        # Build section -> texture mapping using component numbers
        import re as _re
        section_textures = {}  # section_name -> texture_path
        for dc in draw_calls:
            sec = dc.get('section', '')
            if sec in section_textures:
                continue
            # Extract component number from section name
            match = _re.search(r'Component(\\d+)', sec)
            if match:
                comp_num = int(match.group(1))
                texs = comp_textures.get(comp_num, [])
                if texs:
                    section_textures[sec] = texs[0]  # Use first texture as default
                    # Store all textures for this section (for variant switching)
                    dc['_all_textures'] = texs

        # Fallback: use ib_textures if no component mapping found
        ib_textures = {}
        for dc in draw_calls:
            ib = dc.get('ib_resource')
            if not ib or ib in ib_textures:
                continue
            # Find diffuse texture by resource name (works for all game formats)
            texs = dc.get('textures', {})
            tex_ref = None
            for slot_key, res_name in texs.items():
                if not res_name: continue
                res_lower = res_name.lower()
                if ('diffuse' in res_lower and 'normal' not in res_lower and
                    'lightmap' not in res_lower and 'light' not in res_lower):
                    tex_ref = res_name
                    break
            # Fallback: try slot priority
            if not tex_ref:
                for key in ['ps-t1', 'ps-t0', 'diffuse', 'this']:
                    if texs.get(key):
                        candidate = texs[key]
                        cl = candidate.lower()
                        if 'normal' in cl or 'lightmap' in cl or 'light' in cl:
                            continue
                        tex_ref = candidate
                        break
            if tex_ref and _resolve_lookup(resolved, tex_ref):
                ib_textures[ib] = _resolve_lookup(resolved, tex_ref)['path']
            else:
                ib_textures[ib] = None

        if not section_textures and all(v is None for v in ib_textures.values()):
            print(f"  [Migoto] No texture refs found, scanning...")
            tex_files = {}
            for sdir in search_dirs:
                for root, dirs, files in os.walk(sdir):
                    depth = root.replace(sdir, '').count(os.sep)
                    if depth > 2:
                        dirs.clear()
                        continue
                    for f in files:
                        if f.lower().endswith(('.dds', '.png', '.jpg')):
                            fp = os.path.join(root, f)
                            rel = os.path.relpath(fp, sdir).replace('\\', '/').lower()
                            tex_files[rel] = fp

            for ib in ib_textures:
                ib_lower = ib.lower()
                best = None
                for rel, fp in tex_files.items():
                    rel_lower = rel.lower()
                    if any(skip in rel_lower for skip in ['normal', 'lightmap', 'materialmap', 'fx', 'wengine', 'toggle', 'menu', 'slot']):
                        continue
                    best = fp
                    break
                ib_textures[ib] = best
                if best:
                    print(f"  [Migoto] {ib} -> auto: {os.path.basename(best)}")

        # Use INI directory (not parent dirs) for texture scanning
        mod_root = ini_dir
        
        # Create collection
        mod_name = safe_name(os.path.splitext(os.path.basename(ini_path))[0])
        coll = bpy.data.collections.new(mod_name)
        coll['migoto_ini_dir'] = mod_root

        # Scan and store texture file list on collection
        tex_files = []
        for f in os.listdir(mod_root):
            fp = os.path.join(mod_root, f)
            if os.path.isfile(fp) and f.lower().endswith(('.dds', '.png', '.jpg')):
                tex_files.append(fp)
        for entry in os.listdir(mod_root):
            sub = os.path.join(mod_root, entry)
            if not os.path.isdir(sub) or entry.lower() in ('meshes', 'hlsl'):
                continue
            for f in os.listdir(sub):
                fp = os.path.join(sub, f)
                if os.path.isfile(fp) and f.lower().endswith(('.dds', '.png', '.jpg')):
                    tex_files.append(fp)
        # Store texture file list on collection for per-MOD switching
        coll['migoto_tex_files'] = '\n'.join(tex_files)

        context.scene.collection.children.link(coll)

        # Build texture assignment map from INI (all texture types)
        tex_assign_map = {}
        if self.load_special_textures:
            tex_assign_map = build_texture_assignment_map(ini_path, ini_dir, resources, resolved, draw_calls)
            if tex_assign_map:
                total_types = sum(len(v) for v in tex_assign_map.values())
                print(f"  [Migoto] Special textures enabled: {total_types} textures across {len(tex_assign_map)} sections")
        else:
            print(f"  [Migoto] Special textures disabled (enable in import options to load LightMap/Normal/FX)")

        # Create materials per draw call section
        materials = {}
        for dc in draw_calls:
            sec = dc.get('section', '')
            if sec in materials:
                continue
            # Use section texture (from component mapping) or ib texture fallback
            tex_path = section_textures.get(sec)
            if not tex_path:
                ib = dc.get('ib_resource', '')
                tex_path = ib_textures.get(ib)
            mat_name = f"{mod_name}_{safe_name(sec)}"
            is_hair = 'hair' in sec.lower()

            # Use full texture assignments if special textures enabled
            tex_assign = tex_assign_map.get(sec, {})
            if tex_assign:
                materials[sec] = make_material(mat_name, tex_assignments=tex_assign)
                types_str = ', '.join(f"{k}:{os.path.basename(v)}" for k, v in tex_assign.items())
                print(f"  [Migoto] Material {mat_name} -> {types_str}")
            else:
                materials[sec] = make_material(mat_name, diffuse=tex_path, alpha=is_hair)
                if tex_path:
                    print(f"  [Migoto] Material {mat_name} -> {os.path.basename(tex_path)}")

        # Store texture assignments on collection for UI panel access
        coll['migoto_tex_assign'] = str(tex_assign_map) if tex_assign_map else '{}'

        obj_count = 0
        total_draws = len(draw_calls)
        print(f"  [Migoto] Creating meshes ({total_draws} draw calls)...")

        # Progress bar
        wm = context.window_manager
        wm.progress_begin(0, total_draws)

        if self.split_parts:
            for dc_idx, dc in enumerate(draw_calls):
                wm.progress_update(dc_idx)
                ib = dc.get('ib_resource')
                if not ib or not _resolve_lookup(mesh_data, ib):
                    continue

                md = _resolve_lookup(mesh_data, ib)
                positions = md['positions']
                uvs = md['uvs']
                indices = md['indices']

                if not positions or not indices:
                    continue

                # Extract triangles
                tris = []
                if dc['type'] == 'drawindexed':
                    for ti in range(dc['start_index'] // 3, (dc['start_index'] + dc['index_count']) // 3):
                        if ti * 3 + 2 < len(indices):
                            tris.append((indices[ti*3], indices[ti*3+1], indices[ti*3+2]))

                if not tris:
                    continue

                # Material from IB
                mat = materials.get(dc.get('section'))

                obj, cnt = build_object(dc['name'], positions, uvs, tris, mat, coll,
                                        mirror_faces=self.mirror_x or self.mirror_y or self.mirror_z,
                                        blend_weights=md.get('blend_weights'))
                if cnt > 0:
                    obj_count += 1
                    # Tag with variant condition
                    cond = dc.get('condition')
                    if cond:
                        obj['migoto_condition'] = cond
                        # Store parsed condition for toggle evaluation
                        cond_parts = parse_condition(cond)
                        if cond_parts:
                            obj['migoto_cond_parts'] = str(cond_parts)
        else:
            # Merge by IB
            ib_groups = {}
            for dc in draw_calls:
                ib = dc.get('ib_resource')
                if ib not in ib_groups:
                    ib_groups[ib] = []
                ib_groups[ib].append(dc)

            for ib, draws in ib_groups.items():
                if not ib or not _resolve_lookup(mesh_data, ib):
                    continue
                md = _resolve_lookup(mesh_data, ib)
                positions = md['positions']
                uvs = md['uvs']
                indices = md['indices']
                if not positions or not indices:
                    continue

                tris = []
                for dc in draws:
                    if dc['type'] == 'drawindexed':
                        for ti in range(dc['start_index'] // 3, (dc['start_index'] + dc['index_count']) // 3):
                            if ti * 3 + 2 < len(indices):
                                tris.append((indices[ti*3], indices[ti*3+1], indices[ti*3+2]))
                if tris:
                    mat = materials.get(ib)
                    md = _resolve_lookup(mesh_data, ib)
                    obj, cnt = build_object(f"{mod_name}_merged", positions, uvs, tris, mat, coll,
                                            mirror_faces=self.mirror_x or self.mirror_y or self.mirror_z,
                                            blend_weights=md.get('blend_weights') if md else None)
                    if cnt > 0:
                        obj_count += 1

        wm.progress_end()

        # Auto-load game toggles from INI
        toggles = parse_ini_toggles(ini_path)
        if toggles:
            if not hasattr(context.scene, 'migoto_game_toggles'):
                context.scene.migoto_game_toggles = bpy.props.PointerProperty(type=MIGOTO_PG_game_toggles)
            gt = context.scene.migoto_game_toggles
            gt.toggles.clear()
            for t_data in toggles:
                item = gt.toggles.add()
                item.name = t_data['name']
                item.var = t_data['var']
                item.value = t_data['default']
                item.default = t_data['default']
                item.max_val = t_data.get('max', 1)
            # Store INI path
            coll['migoto_ini_path'] = ini_path
            print(f"  [Migoto] Loaded {len(toggles)} game toggles")

        self.report({'INFO'}, f"导入 {obj_count} 个对象 / Imported {obj_count} objects")
        return {'FINISHED'}


def menu_fn(self, context):
    self.layout.operator(IMPORT_OT_3dmigoto.bl_idname, text="3Dmigoto 模型 / 3Dmigoto Model (.ini)")


# ============================================================
# Variant Texture Switcher UI
# ============================================================

class MIGOTO_PG_texture_item(bpy.types.PropertyGroup):
    """贴图列表条目 / Texture list item"""
    name: bpy.props.StringProperty(name="Name")
    filepath: bpy.props.StringProperty(name="Path")
    category: bpy.props.StringProperty(name="Category")
    preview_icon: bpy.props.IntProperty(name="Preview Icon", default=0)


class MIGOTO_PG_texture_state(bpy.types.PropertyGroup):
    """贴图浏览器状态"""
    active_index: bpy.props.IntProperty(name="Active Texture", default=0)
    expanded_mod: bpy.props.StringProperty(name="Expanded MOD", default='')


class MIGOTO_PG_game_toggle(bpy.types.PropertyGroup):
    """游戏 MOD 开关变量"""
    name: bpy.props.StringProperty(name="显示名")
    var: bpy.props.StringProperty(name="变量名")
    value: bpy.props.IntProperty(name="值", default=0, min=0, max=10)
    default: bpy.props.IntProperty(name="默认值", default=0)
    max_val: bpy.props.IntProperty(name="最大值", default=1)


class MIGOTO_PG_game_toggles(bpy.types.PropertyGroup):
    """所有游戏开关"""
    toggles: bpy.props.CollectionProperty(type=MIGOTO_PG_game_toggle)
    active_index: bpy.props.IntProperty(default=0)


class MIGOTO_OT_load_textures(bpy.types.Operator):
    """加载选中对象所属MOD的贴图列表 / Load textures for selected object's MOD"""
    bl_idname = "migoto.load_textures"
    bl_label = "加载贴图列表 / Load Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        # Find mod directory from selected object's collection
        mod_dir = None
        mod_name = None
        obj = context.active_object
        if obj:
            for coll in obj.users_collection:
                if 'migoto_ini_dir' in coll:
                    mod_dir = coll['migoto_ini_dir']
                    mod_name = coll.name
                    break

        if not mod_dir:
            for coll in bpy.data.collections:
                if 'migoto_ini_dir' in coll:
                    mod_dir = coll['migoto_ini_dir']
                    mod_name = coll.name
                    break

        if not mod_dir or not os.path.isdir(mod_dir):
            self.report({'ERROR'}, '未找到模型目录')
            return {'CANCELLED'}

        # Initialize texture list
        if not hasattr(scene, 'migoto_textures'):
            scene.migoto_textures = bpy.props.CollectionProperty(type=MIGOTO_PG_texture_item)
        scene.migoto_textures.clear()

        # Reset preview index
        if hasattr(scene, 'migoto_tex_state'):
            scene.migoto_tex_state.active_index = 0

        # Recreate preview collection (avoid stale icons)
        if 'default' in _preview_collections:
            bpy.utils.previews.remove(_preview_collections['default'])
        _preview_collections['default'] = bpy.utils.previews.new()
        pcoll = _preview_collections['default']

        # Scan mod directory
        tex_files = []
        for f in os.listdir(mod_dir):
            fp = os.path.join(mod_dir, f)
            if os.path.isfile(fp) and f.lower().endswith(('.dds', '.png', '.jpg')):
                tex_files.append((fp, 'root'))
        for entry in os.listdir(mod_dir):
            sub = os.path.join(mod_dir, entry)
            if not os.path.isdir(sub) or entry.lower() in ('meshes', 'hlsl'):
                continue
            for f in os.listdir(sub):
                fp = os.path.join(sub, f)
                if os.path.isfile(fp) and f.lower().endswith(('.dds', '.png', '.jpg')):
                    tex_files.append((fp, entry))

        # Load previews
        for fp, category in tex_files:
            item = scene.migoto_textures.add()
            item.name = os.path.splitext(os.path.basename(fp))[0]
            item.filepath = fp
            item.category = category

            preview_loaded = False
            try:
                preview = pcoll.load(fp, fp, 'IMAGE')
                item.preview_icon = preview.icon_id
                preview_loaded = True
            except Exception:
                pass

            if not preview_loaded and fp.lower().endswith('.dds'):
                try:
                    from PIL import Image as PILImage
                    import tempfile
                    temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
                    os.makedirs(temp_dir, exist_ok=True)
                    png_name = os.path.splitext(os.path.basename(fp))[0] + '_preview_fix.png'
                    png_path = os.path.join(temp_dir, png_name)
                    if not os.path.exists(png_path):
                        pil_img = PILImage.open(fp)
                        if pil_img.mode == 'RGBA':
                            r, g, b, a = pil_img.split()
                            a_data = list(a.getdata())
                            zero_ratio = sum(1 for v in a_data if v < 10) / len(a_data)
                            if zero_ratio > 0.3:
                                a = PILImage.new('L', pil_img.size, 255)
                                pil_img = PILImage.merge('RGBA', (r, g, b, a))
                        pil_img.save(png_path)
                    preview = pcoll.load(fp, png_path, 'IMAGE')
                    item.preview_icon = preview.icon_id
                except Exception:
                    item.preview_icon = 0

        self.report({'INFO'}, f'[{mod_name}] 加载 {len(scene.migoto_textures)} 张贴图')
        return {'FINISHED'}

class MIGOTO_OT_apply_texture(bpy.types.Operator):
    """应用贴图到选中对象 / Apply texture to selected object"""
    bl_idname = "migoto.apply_texture"
    bl_label = "应用贴图 / Apply Texture"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中一个网格对象')
            return {'CANCELLED'}

        fp = self.filepath
        if not os.path.exists(fp):
            self.report({'ERROR'}, '文件不存在')
            return {'CANCELLED'}

        # For DDS, convert to temp PNG
        if fp.lower().endswith('.dds'):
            try:
                from PIL import Image as PILImage
                import tempfile
                temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex'); os.makedirs(temp_dir, exist_ok=True)
                png_name = os.path.splitext(os.path.basename(fp))[0] + '.png'
                png_path = os.path.join(temp_dir, png_name)
                PILImage.open(fp).save(png_path)
                fp = png_path
            except Exception:
                pass

        # Load image
        img_name = os.path.basename(fp)
        if img_name in bpy.data.images:
            img = bpy.data.images[img_name]
        else:
            img = bpy.data.images.load(fp)
            img.name = img_name

        # Find or create material
        mat = obj.active_material
        if not mat:
            mat = bpy.data.materials.new(name=obj.name + '_Material')
            mat.use_nodes = True
            obj.data.materials.append(mat)

        if not mat.use_nodes:
            mat.use_nodes = True

        # Find image texture node
        tex_node = None
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                tex_node = node
                break

        if not tex_node:
            tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
            tex_node.location = (-300, 0)
            for node in mat.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    mat.node_tree.links.new(tex_node.outputs['Color'], node.inputs['Base Color'])
                    break

        tex_node.image = img

        self.report({'INFO'}, f'已应用: {img_name}')
        return {'FINISHED'}


class MIGOTO_OT_select_texture(bpy.types.Operator):
    """选择贴图预览 / Select texture for preview"""
    bl_idname = "migoto.select_texture"
    bl_label = "Preview"
    bl_options = {'REGISTER'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if hasattr(scene, 'migoto_tex_state'):
            scene.migoto_tex_state.active_index = self.index
        return {'FINISHED'}


class MIGOTO_OT_apply_texture_from_list(bpy.types.Operator):
    """从列表应用贴图 / Apply texture from list"""
    bl_idname = "migoto.apply_texture_from_list"
    bl_label = "Apply"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_textures') or self.index >= len(scene.migoto_textures):
            return {'CANCELLED'}
        item = scene.migoto_textures[self.index]

        # Update preview index
        if hasattr(scene, 'migoto_tex_state'):
            scene.migoto_tex_state.active_index = self.index

        # Apply directly (load_dds handles DDS conversion to temp)
        bpy.ops.migoto.apply_texture(filepath=item.filepath)
        return {'FINISHED'}


class MIGOTO_OT_clear_textures(bpy.types.Operator):
    """清空贴图列表 / Clear texture list"""
    bl_idname = "migoto.clear_textures"
    bl_label = "清空贴图 / Clear Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        if hasattr(scene, 'migoto_textures'):
            scene.migoto_textures.clear()
        if hasattr(scene, 'migoto_tex_state'):
            scene.migoto_tex_state.active_index = 0
        self.report({'INFO'}, '已清空贴图列表')
        return {'FINISHED'}


class MIGOTO_OT_toggle_mod_textures(bpy.types.Operator):
    """展开/收起MOD贴图列表 / Expand/collapse MOD texture list"""
    bl_idname = "migoto.toggle_mod_textures"
    bl_label = "Toggle MOD Textures"
    bl_options = {'REGISTER'}

    mod_name: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_tex_state'):
            scene.migoto_tex_state = bpy.props.PointerProperty(type=MIGOTO_PG_texture_state)
        tex_state = scene.migoto_tex_state
        if tex_state.expanded_mod == self.mod_name:
            tex_state.expanded_mod = ''  # Collapse
        else:
            tex_state.expanded_mod = self.mod_name  # Expand
        return {'FINISHED'}


class MIGOTO_OT_select_mod_texture(bpy.types.Operator):
    """选择贴图预览 / Select texture for preview"""
    bl_idname = "migoto.select_mod_texture"
    bl_label = "Preview"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty()
    tex_name: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_tex_state'):
            scene.migoto_tex_state = bpy.props.PointerProperty(type=MIGOTO_PG_texture_state)
        tex_state = scene.migoto_tex_state

        # Load preview image
        img_key = f'migoto_preview_{self.tex_name}'
        if img_key not in bpy.data.images and os.path.exists(self.filepath):
            try:
                fp = self.filepath
                if fp.lower().endswith('.dds'):
                    from PIL import Image as PILImage
                    import tempfile
                    temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
                    os.makedirs(temp_dir, exist_ok=True)
                    png_name = os.path.splitext(os.path.basename(fp))[0] + '_preview_fix.png'
                    png_path = os.path.join(temp_dir, png_name)
                    if not os.path.exists(png_path):
                        pil_img = PILImage.open(fp)
                        if pil_img.mode == 'RGBA':
                            r, g, b, a = pil_img.split()
                            a_data = list(a.getdata())
                            zero_ratio = sum(1 for v in a_data if v < 10) / len(a_data)
                            if zero_ratio > 0.3:
                                a = PILImage.new('L', pil_img.size, 255)
                                pil_img = PILImage.merge('RGBA', (r, g, b, a))
                        pil_img.save(png_path)
                    fp = png_path
                img = bpy.data.images.load(fp)
                img.name = img_key
                img.alpha_mode = 'NONE'
            except Exception:
                pass

        # Update preview index in the texture list
        if hasattr(scene, 'migoto_textures'):
            for i, item in enumerate(scene.migoto_textures):
                if item.name == self.tex_name:
                    tex_state.active_index = i
                    break

        return {'FINISHED'}


class MIGOTO_OT_apply_mod_texture(bpy.types.Operator):
    """应用贴图到选中对象 / Apply texture to selected object"""
    bl_idname = "migoto.apply_mod_texture"
    bl_label = "Apply Texture"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中一个网格对象')
            return {'CANCELLED'}

        fp = self.filepath
        if not os.path.exists(fp):
            self.report({'ERROR'}, '文件不存在')
            return {'CANCELLED'}

        # For DDS, convert to temp PNG
        if fp.lower().endswith('.dds'):
            try:
                from PIL import Image as PILImage
                import tempfile
                temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
                os.makedirs(temp_dir, exist_ok=True)
                png_name = os.path.splitext(os.path.basename(fp))[0] + '_preview_fix.png'
                png_path = os.path.join(temp_dir, png_name)
                if not os.path.exists(png_path):
                    pil_img = PILImage.open(fp)
                    if pil_img.mode == 'RGBA':
                        r, g, b, a = pil_img.split()
                        a_data = list(a.getdata())
                        zero_ratio = sum(1 for v in a_data if v < 10) / len(a_data)
                        if zero_ratio > 0.3:
                            a = PILImage.new('L', pil_img.size, 255)
                            pil_img = PILImage.merge('RGBA', (r, g, b, a))
                    pil_img.save(png_path)
                fp = png_path
            except Exception:
                pass

        # Load image
        img_name = os.path.basename(fp)
        if img_name in bpy.data.images:
            img = bpy.data.images[img_name]
        else:
            img = bpy.data.images.load(fp)
            img.name = img_name

        # Find or create material
        mat = obj.active_material
        if not mat:
            mat = bpy.data.materials.new(name=obj.name + '_Material')
            mat.use_nodes = True
            obj.data.materials.append(mat)
        if not mat.use_nodes:
            mat.use_nodes = True

        # Find texture node
        bsdf = None
        tex_node = None
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                bsdf = node
            if node.type == 'TEX_IMAGE':
                tex_node = node

        if not tex_node:
            tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
            tex_node.location = (-600, 0)

        tex_node.image = img

        # Connect to BSDF
        if bsdf:
            # Clear existing connections from this tex node
            for link in list(mat.node_tree.links):
                if link.from_node == tex_node:
                    mat.node_tree.links.remove(link)
            mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])

        self.report({'INFO'}, f'已应用: {img_name}')
        return {'FINISHED'}


class MIGOTO_OT_export_textures(bpy.types.Operator):
    """Export all textures to a folder (DDS auto-converts to PNG)"""
    bl_idname = "migoto.export_textures"
    bl_label = "导出贴图 / Export Textures"
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(
        name="输出目录 / Output Directory",
        description="导出贴图的目标文件夹 / Folder to export textures to",
        subtype='DIR_PATH',
    )

    def execute(self, context):
        out_dir = self.directory
        if not out_dir or not os.path.isdir(out_dir):
            self.report({'ERROR'}, 'Invalid output directory')
            return {'CANCELLED'}

        exported = 0
        skipped = 0
        seen = set()

        def export_file(src):
            nonlocal exported, skipped
            if not src or not os.path.exists(src):
                return
            fname = os.path.basename(src)
            if fname in seen:
                return
            seen.add(fname)

            # DDS -> PNG conversion
            if src.lower().endswith('.dds'):
                try:
                    from PIL import Image as PILImage
                    png_name = os.path.splitext(fname)[0] + '.png'
                    dst = os.path.join(out_dir, png_name)
                    PILImage.open(src).save(dst)
                    exported += 1
                    print(f"  [Migoto] {fname} -> {png_name}")
                except ImportError:
                    print(f"  [Migoto] Pillow not installed, cannot convert {fname}")
                except Exception as e:
                    print(f"  [Migoto] Convert failed: {fname}: {e}")
            else:
                # Non-DDS: copy as-is
                import shutil
                dst = os.path.join(out_dir, fname)
                try:
                    shutil.copy2(src, dst)
                    exported += 1
                    print(f"  [Migoto] {fname}")
                except Exception as e:
                    print(f"  [Migoto] Copy failed: {fname}: {e}")

        # Collect from materials
        for mat in bpy.data.materials:
            if not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    export_file(bpy.path.abspath(node.image.filepath))

        # Collect from variants
        if hasattr(context.scene, 'migoto_variants'):
            for mat_var in context.scene.migoto_variants.materials:
                for variant in mat_var.variants:
                    export_file(variant.filepath)

        self.report({'INFO'}, f'Exported {exported} textures to {out_dir}')
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MIGOTO_PG_mesh_group_item(bpy.types.PropertyGroup):
    """单个对象在分组中的条目"""
    obj_name: bpy.props.StringProperty(name="Object Name")


class MIGOTO_PG_mesh_group(bpy.types.PropertyGroup):
    """网格变体分组"""
    name: bpy.props.StringProperty(name="分组名 / Group Name", default="Group")
    items: bpy.props.CollectionProperty(type=MIGOTO_PG_mesh_group_item)
    active_index: bpy.props.IntProperty(name="Active", default=0)


class MIGOTO_PG_mesh_groups(bpy.types.PropertyGroup):
    """所有分组"""
    groups: bpy.props.CollectionProperty(type=MIGOTO_PG_mesh_group)
    active_group: bpy.props.IntProperty(name="Active Group", default=0)


class MIGOTO_OT_add_group(bpy.types.Operator):
    """添加分组 / Add Group"""
    bl_idname = "migoto.add_group"
    bl_label = "Add Group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_mesh_groups'):
            scene.migoto_mesh_groups = bpy.props.PointerProperty(type=MIGOTO_PG_mesh_groups)
        mg = scene.migoto_mesh_groups
        g = mg.groups.add()
        g.name = f"分组 {len(mg.groups)}"
        mg.active_group = len(mg.groups) - 1
        return {'FINISHED'}


class MIGOTO_OT_remove_group(bpy.types.Operator):
    """删除分组 / Remove Group"""
    bl_idname = "migoto.remove_group"
    bl_label = "Remove Group"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        mg = context.scene.migoto_mesh_groups
        if 0 <= self.index < len(mg.groups):
            # Unhide all objects in this group
            group = mg.groups[self.index]
            for item in group.items:
                obj = bpy.data.objects.get(item.obj_name)
                if obj:
                    obj.hide_viewport = False
                    obj.hide_render = False
            mg.groups.remove(self.index)
            # Adjust active_group
            if mg.active_group >= len(mg.groups):
                mg.active_group = max(0, len(mg.groups) - 1)
        return {'FINISHED'}


class MIGOTO_OT_add_obj_to_group(bpy.types.Operator):
    """添加选中对象到分组 / Add selected objects to group"""
    bl_idname = "migoto.add_obj_to_group"
    bl_label = "Add Selected"
    bl_options = {'REGISTER', 'UNDO'}

    group_index: bpy.props.IntProperty()

    def execute(self, context):
        mg = context.scene.migoto_mesh_groups
        if self.group_index >= len(mg.groups):
            return {'CANCELLED'}
        group = mg.groups[self.group_index]
        existing = {item.obj_name for item in group.items}
        added = 0
        for obj in context.selected_objects:
            if obj.type == 'MESH' and obj.name not in existing:
                item = group.items.add()
                item.obj_name = obj.name
                added += 1
        # Hide all but first
        if len(group.items) > 1:
            for i, item in enumerate(group.items):
                obj = bpy.data.objects.get(item.obj_name)
                if obj:
                    obj.hide_viewport = (i != 0)
                    obj.hide_render = (i != 0)
                    group.active_index = 0
        self.report({'INFO'}, f'添加 {added} 个对象 / Added {added} objects')
        return {'FINISHED'}


class MIGOTO_OT_remove_obj_from_group(bpy.types.Operator):
    """从分组移除对象 / Remove object from group"""
    bl_idname = "migoto.remove_obj_from_group"
    bl_label = "Remove"
    bl_options = {'REGISTER', 'UNDO'}

    group_index: bpy.props.IntProperty()
    item_index: bpy.props.IntProperty()

    def execute(self, context):
        mg = context.scene.migoto_mesh_groups
        if self.group_index >= len(mg.groups):
            return {'CANCELLED'}
        group = mg.groups[self.group_index]
        if 0 <= self.item_index < len(group.items):
            # Unhide this object
            obj = bpy.data.objects.get(group.items[self.item_index].obj_name)
            if obj:
                obj.hide_viewport = False
                obj.hide_render = False
            group.items.remove(self.item_index)
        return {'FINISHED'}


class MIGOTO_OT_toggle_obj_visibility(bpy.types.Operator):
    """切换对象可见性 / Toggle object visibility"""
    bl_idname = "migoto.toggle_obj_visibility"
    bl_label = "Toggle"
    bl_options = {'REGISTER', 'UNDO'}

    obj_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.obj_name)
        if obj:
            obj.hide_viewport = not obj.hide_viewport
            obj.hide_render = not obj.hide_render
        return {'FINISHED'}


class MIGOTO_OT_highlight_obj(bpy.types.Operator):
    """高亮选中对象（3D视图描边） / Highlight object in viewport"""
    bl_idname = "migoto.highlight_obj"
    bl_label = "Highlight"
    bl_options = {'REGISTER', 'UNDO'}

    obj_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.obj_name)
        if not obj:
            self.report({'WARNING'}, f'Object not found: {self.obj_name}')
            return {'CANCELLED'}
        # Deselect all
        bpy.ops.object.select_all(action='DESELECT')
        # Make visible if hidden
        obj.hide_viewport = False
        # Select and set active (Blender draws orange outline)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        # Focus viewport on the object
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                override = context.copy()
                override['area'] = area
                override['region'] = area.regions[-1]
                try:
                    with context.temp_override(**override):
                        bpy.ops.view3d.view_selected(use_all_regions=False)
                except Exception:
                    pass
                break
        return {'FINISHED'}


class MIGOTO_OT_show_all_in_group(bpy.types.Operator):
    """显示组内全部 / Show all in group"""
    bl_idname = "migoto.show_all_in_group"
    bl_label = "Show All"
    bl_options = {'REGISTER', 'UNDO'}

    group_index: bpy.props.IntProperty()

    def execute(self, context):
        mg = context.scene.migoto_mesh_groups
        if self.group_index < len(mg.groups):
            for item in mg.groups[self.group_index].items:
                obj = bpy.data.objects.get(item.obj_name)
                if obj:
                    obj.hide_viewport = False
                    obj.hide_render = False
        return {'FINISHED'}


class MIGOTO_OT_hide_all_in_group(bpy.types.Operator):
    """隐藏组内全部 / Hide all in group"""
    bl_idname = "migoto.hide_all_in_group"
    bl_label = "Hide All"
    bl_options = {'REGISTER', 'UNDO'}

    group_index: bpy.props.IntProperty()

    def execute(self, context):
        mg = context.scene.migoto_mesh_groups
        if self.group_index < len(mg.groups):
            for item in mg.groups[self.group_index].items:
                obj = bpy.data.objects.get(item.obj_name)
                if obj:
                    obj.hide_viewport = True
                    obj.hide_render = True
        return {'FINISHED'}


class MIGOTO_OT_select_group(bpy.types.Operator):
    """选择分组 / Select group"""
    bl_idname = "migoto.select_group"
    bl_label = "Select Group"
    bl_options = {'REGISTER'}

    group_index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if hasattr(scene, 'migoto_mesh_groups'):
            mg = scene.migoto_mesh_groups
            if 0 <= self.group_index < len(mg.groups):
                mg.active_group = self.group_index
        return {'FINISHED'}


class MIGOTO_OT_rename_group(bpy.types.Operator):
    """重命名分组 / Rename Group"""
    bl_idname = "migoto.rename_group"
    bl_label = "Rename"
    bl_options = {'REGISTER', 'UNDO'}

    group_index: bpy.props.IntProperty()
    new_name: bpy.props.StringProperty(name="Name", default="Group")

    def execute(self, context):
        mg = context.scene.migoto_mesh_groups
        if 0 <= self.group_index < len(mg.groups):
            mg.groups[self.group_index].name = self.new_name
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class MIGOTO_OT_auto_group(bpy.types.Operator):
    """自动按INI条件分组 / Auto-group by INI conditions"""
    bl_idname = "migoto.auto_group"
    bl_label = "Auto Group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_mesh_groups'):
            scene.migoto_mesh_groups = bpy.props.PointerProperty(type=MIGOTO_PG_mesh_groups)
        mg = scene.migoto_mesh_groups

        # Find collection from selected object
        target_coll = None
        obj = context.active_object
        if obj:
            for coll in obj.users_collection:
                if 'migoto_ini_dir' in coll:
                    target_coll = coll
                    break

        # Fallback: first migoto collection
        if not target_coll:
            for coll in bpy.data.collections:
                if 'migoto_ini_dir' in coll:
                    target_coll = coll
                    break

        if not target_coll:
            self.report({'INFO'}, '未找到导入模型 / No imported model found')
            return {'CANCELLED'}

        # Remove existing groups for this collection only
        prefix = f'[{target_coll.name}]'
        to_remove = [i for i, g in enumerate(mg.groups) if g.name.startswith(prefix)]
        for i in reversed(to_remove):
            mg.groups.remove(i)

        # Parse conditions from this collection's objects
        import re as _re
        var_groups = {}  # var_name -> {value -> [obj_name, ...]}
        no_cond_objects = []

        for obj in target_coll.objects:
            if obj.type != 'MESH':
                continue
            cond = obj.get('migoto_condition', None)
            if cond:
                match = _re.match(r'(\$\w+)\s*==\s*', cond)
                if match:
                    var_name = match.group(1)
                    value = cond
                else:
                    var_name = 'other'
                    value = cond
                if var_name not in var_groups:
                    var_groups[var_name] = {}
                if value not in var_groups[var_name]:
                    var_groups[var_name][value] = []
                var_groups[var_name][value].append(obj.name)
            else:
                no_cond_objects.append(obj.name)

        if not var_groups and not no_cond_objects:
            self.report({'INFO'}, '未找到变体条件 / No variant conditions found')
            return {'CANCELLED'}

        # Create groups with MOD prefix
        for var_name, values in sorted(var_groups.items()):
            g = mg.groups.add()
            g.name = f'{prefix} {var_name}'
            for value, obj_names in sorted(values.items()):
                for oname in obj_names:
                    item = g.items.add()
                    item.obj_name = oname
            for item in g.items:
                o = bpy.data.objects.get(item.obj_name)
                if o:
                    o.hide_viewport = False
                    o.hide_render = False

        if no_cond_objects:
            g = mg.groups.add()
            g.name = f'{prefix} 常显 / Always Visible'
            for oname in no_cond_objects:
                item = g.items.add()
                item.obj_name = oname

        self.report({'INFO'}, f'[{target_coll.name}] 创建 {len(mg.groups)} 个分组')
        return {'FINISHED'}


class MIGOTO_UL_mesh_group_items(bpy.types.UIList):
    """网格变体列表项（带勾选框和高亮按钮）"""
    bl_idname = "MIGOTO_UL_mesh_group_items"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        obj = bpy.data.objects.get(item.obj_name)
        row = layout.row(align=True)

        # Visibility toggle
        is_visible = obj and not obj.hide_viewport
        vis_icon = 'CHECKBOX_HLT' if is_visible else 'CHECKBOX_DEHLT'
        op = row.operator('migoto.toggle_obj_visibility', text='', icon=vis_icon)
        op.obj_name = item.obj_name

        # Object name
        row.label(text=item.obj_name, icon='OBJECT_DATA')

        # Highlight button
        op = row.operator('migoto.highlight_obj', text='', icon='RESTRICT_SELECT_OFF')
        op.obj_name = item.obj_name

        # Remove button - find group index by matching data (the group) in mesh_groups
        mg = context.scene.migoto_mesh_groups
        g_idx = -1
        for gi, g in enumerate(mg.groups):
            if g.name == data.name:
                g_idx = gi
                break
        if g_idx >= 0:
            op = row.operator('migoto.remove_obj_from_group', text='', icon='TRASH')
            op.group_index = g_idx
            op.item_index = index


class MIGOTO_UL_mesh_groups(bpy.types.UIList):
    """分组列表（左侧选择）"""
    bl_idname = "MIGOTO_UL_mesh_groups"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon='GROUP')
        # Show item count
        row.label(text=f'({len(item.items)})')


class MIGOTO_UL_texture_list(bpy.types.UIList):
    """贴图列表（带预览和应用按钮）"""
    bl_idname = "MIGOTO_UL_texture_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)

        # Preview icon
        if item.preview_icon:
            row.label(text='', icon_value=item.preview_icon)

        # Category tag (short)
        cat = item.category or ''
        if cat and cat != 'root':
            row.label(text=f'[{cat[:6]}]', icon='FILE_FOLDER')

        # Texture name - click to preview
        op = row.operator('migoto.select_texture', text=item.name, icon='IMAGE_DATA')
        op.index = index

        # Apply button
        op = row.operator('migoto.apply_texture_from_list', text='', icon='IMPORT')
        op.index = index

    def filter_items(self, context, data, propname):
        """Support filtering by search string."""
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        # If there's a filter name, filter by it
        if self.filter_name:
            flt = self.filter_name.lower()
            for i, item in enumerate(items):
                if flt not in item.name.lower() and flt not in (item.category or '').lower():
                    flags[i] &= ~self.bitflag_filter_item
        return flags, []


class MIGOTO_PT_mesh_groups(bpy.types.Panel):
    """网格变体分组面板"""
    bl_label = "网格变体 / Mesh Variants"
    bl_idname = "MIGOTO_PT_mesh_groups"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'
    bl_options = {'DEFAULT_CLOSED'}

    def _draw_group(self, layout, mg, g_idx, group, display_name):
        """Draw a single group with its items and controls."""
        grp_box = layout.box()
        is_active = (g_idx == mg.active_group)

        # Group header row
        row = grp_box.row(align=True)
        row.scale_y = 1.2
        icon = 'TRIA_DOWN' if is_active else 'TRIA_RIGHT'
        op = row.operator('migoto.select_group', text=display_name, icon=icon)
        op.group_index = g_idx
        # Rename button
        op = row.operator('migoto.rename_group', text='', icon='GREASEPENCIL')
        op.group_index = g_idx
        # Remove button
        op = row.operator('migoto.remove_group', text='', icon='X')
        op.index = g_idx

        if not is_active:
            return

        # Add selected button
        op = grp_box.operator('migoto.add_obj_to_group', text='添加选中 / Add Selected', icon='ADD')
        op.group_index = g_idx

        # Show items
        if len(group.items) > 0:
            for i_idx, item in enumerate(group.items):
                obj = bpy.data.objects.get(item.obj_name)
                row = grp_box.row(align=True)
                is_visible = obj and not obj.hide_viewport
                vis_icon = 'CHECKBOX_HLT' if is_visible else 'CHECKBOX_DEHLT'
                op = row.operator('migoto.toggle_obj_visibility', text='', icon=vis_icon)
                op.obj_name = item.obj_name
                row.label(text=item.obj_name)
                op = row.operator('migoto.highlight_obj', text='', icon='RESTRICT_SELECT_OFF')
                op.obj_name = item.obj_name
                # Remove from group
                op = row.operator('migoto.remove_obj_from_group', text='', icon='TRASH')
                op.group_index = g_idx
                op.item_index = i_idx

            # Show all / Hide all
            row = grp_box.row(align=True)
            op = row.operator('migoto.show_all_in_group', text='全部显示', icon='HIDE_OFF')
            op.group_index = g_idx
            op = row.operator('migoto.hide_all_in_group', text='全部隐藏', icon='HIDE_ON')
            op.group_index = g_idx
        else:
            grp_box.label(text='无部件 / No items')

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Auto-group button
        row = layout.row(align=True)
        row.operator('migoto.auto_group', text='自动分组 / Auto Group', icon='FILTER')
        row.operator('migoto.add_group', text='添加分组 / Add Group', icon='ADD')

        if not hasattr(scene, 'migoto_mesh_groups'):
            return
        mg = scene.migoto_mesh_groups

        if not mg.groups:
            layout.label(text='无分组 / No groups')
            return

        # Group by MOD prefix: extract [MOD名] from group name
        mod_groups = {}  # mod_prefix -> [(group_index, group, display_name)]
        no_prefix = []
        for g_idx, group in enumerate(mg.groups):
            if group.name.startswith('['):
                end = group.name.index(']')
                mod_prefix = group.name[:end+1]
                display_name = group.name[end+1:].strip()
                if mod_prefix not in mod_groups:
                    mod_groups[mod_prefix] = []
                mod_groups[mod_prefix].append((g_idx, group, display_name))
            else:
                no_prefix.append((g_idx, group, group.name))

        # Draw each MOD in its own box
        for mod_prefix, items in mod_groups.items():
            box = layout.box()
            row = box.row()
            row.scale_y = 1.3
            row.label(text=f'🎮 {mod_prefix}', icon='OUTLINER_COLLECTION')
            for g_idx, group, display_name in items:
                self._draw_group(box, mg, g_idx, group, display_name)

        # Groups without MOD prefix
        if no_prefix:
            box = layout.box()
            row = box.row()
            row.scale_y = 1.3
            row.label(text='其他 / Other', icon='GROUP')
            for g_idx, group, display_name in no_prefix:
                self._draw_group(box, mg, g_idx, group, display_name)


class MIGOTO_PT_panel(bpy.types.Panel):
    """3Dmigoto 主面板"""
    bl_label = "3Dmigoto"
    bl_idname = "MIGOTO_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Import button
        layout.operator('import_scene.migoto_model', text='导入模型 / Import Model', icon='IMPORT')
        layout.separator()

        # Export textures
        layout.operator('migoto.export_textures', text='导出贴图 / Export Textures', icon='EXPORT')
        layout.separator()

        # Texture browser (for selected object)
        obj = context.active_object
        if obj and obj.type == 'MESH':
            box = layout.box()
            box.label(text=f'贴图浏览器 / Texture Browser', icon='TEXTURE')
            box.label(text=f'对象: {obj.name}', icon='OBJECT_DATA')

            # Current texture
            if obj.active_material and obj.active_material.use_nodes:
                for node in obj.active_material.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        img = node.image
                        box.label(text=f'当前: {img.name}', icon='IMAGE_DATA')
                        break

            # Show which MOD
            mod_name = None
            for coll in obj.users_collection:
                if 'migoto_ini_dir' in coll:
                    mod_name = coll.name
                    break
            if mod_name:
                box.label(text=f'MOD: {mod_name}', icon='OUTLINER_COLLECTION')

            # Load/Clear buttons
            row = box.row(align=True)
            row.operator('migoto.load_textures', text='加载贴图 / Load', icon='FILE_FOLDER')
            row.operator('migoto.clear_textures', text='清空 / Clear', icon='X')

            if not hasattr(scene, 'migoto_textures') or len(scene.migoto_textures) == 0:
                for coll in bpy.data.collections:
                    if 'migoto_ini_dir' in coll:
                        box.label(text='提示: 点击上方按钮加载贴图列表', icon='INFO')
                        break
                return

            if not hasattr(scene, 'migoto_tex_state'):
                scene.migoto_tex_state = bpy.props.PointerProperty(type=MIGOTO_PG_texture_state)
            tex_state = scene.migoto_tex_state

            # Large preview of selected texture
            if 0 <= tex_state.active_index < len(scene.migoto_textures):
                active_item = scene.migoto_textures[tex_state.active_index]
                preview_box = box.box()
                preview_box.label(text=f'预览: {active_item.name}', icon='IMAGE_DATA')

                preview_img = None
                img_key = f'migoto_preview_{active_item.name}'
                if img_key in bpy.data.images:
                    preview_img = bpy.data.images[img_key]
                elif os.path.exists(active_item.filepath):
                    try:
                        fp = active_item.filepath
                        if fp.lower().endswith('.dds'):
                            from PIL import Image as PILImage
                            import tempfile
                            temp_dir = os.path.join(tempfile.gettempdir(), 'migoto_tex')
                            os.makedirs(temp_dir, exist_ok=True)
                            png_name = os.path.splitext(os.path.basename(fp))[0] + '_preview_fix.png'
                            png_path = os.path.join(temp_dir, png_name)
                            if not os.path.exists(png_path):
                                pil_img = PILImage.open(fp)
                                if pil_img.mode == 'RGBA':
                                    r, g, b, a = pil_img.split()
                                    a_data = list(a.getdata())
                                    zero_ratio = sum(1 for v in a_data if v < 10) / len(a_data)
                                    if zero_ratio > 0.3:
                                        a = PILImage.new('L', pil_img.size, 255)
                                        pil_img = PILImage.merge('RGBA', (r, g, b, a))
                                elif pil_img.mode == 'RGB':
                                    pil_img = pil_img.convert('RGBA')
                                pil_img.save(png_path)
                            fp = png_path
                        preview_img = bpy.data.images.load(fp)
                        preview_img.name = img_key
                        preview_img.alpha_mode = 'NONE'
                    except Exception:
                        pass

                if preview_img and preview_img.preview:
                    preview_box.template_icon(icon_value=preview_img.preview.icon_id, scale=6.0)
                elif active_item.preview_icon:
                    preview_box.template_icon(icon_value=active_item.preview_icon, scale=6.0)
                else:
                    preview_box.label(text='无预览 / No preview')

            # Texture list with scrollbar
            if len(scene.migoto_textures) > 0:
                row = box.row()
                row.template_list(
                    'MIGOTO_UL_texture_list',
                    '',
                    scene,
                    'migoto_textures',
                    tex_state,
                    'active_index',
                    rows=min(max(len(scene.migoto_textures), 3), 12),
                    maxrows=12,
                )
            else:
                box.label(text='无贴图 / No textures')
        else:
            layout.label(text='请选中一个网格对象')


class MIGOTO_OT_toggle_image_alpha(bpy.types.Operator):
    """切换贴图Alpha模式 / Toggle image alpha mode"""
    bl_idname = "migoto.toggle_image_alpha"
    bl_label = "Toggle Alpha"
    bl_options = {'REGISTER', 'UNDO'}

    image_name: bpy.props.StringProperty()

    def execute(self, context):
        img = bpy.data.images.get(self.image_name)
        if not img:
            self.report({'WARNING'}, f'Image not found: {self.image_name}')
            return {'CANCELLED'}

        # Cycle: NONE -> STRAIGHT -> PREMUL -> NONE
        if img.alpha_mode == 'NONE':
            img.alpha_mode = 'STRAIGHT'
        elif img.alpha_mode == 'STRAIGHT':
            img.alpha_mode = 'PREMUL'
        else:
            img.alpha_mode = 'NONE'

        # Force viewport update
        img.update()
        for area in context.screen.areas:
            area.tag_redraw()

        return {'FINISHED'}


class MIGOTO_OT_change_tex_slot(bpy.types.Operator):
    """修改贴图槽位 / Change texture slot"""
    bl_idname = "migoto.change_tex_slot"
    bl_label = "Change Texture"
    bl_options = {'REGISTER', 'UNDO'}

    mat_name: bpy.props.StringProperty()
    tex_type: bpy.props.StringProperty()  # diffuse, lightmap, normal, etc.
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = bpy.data.materials.get(self.mat_name)
        if not mat or not mat.use_nodes:
            self.report({'ERROR'}, f'Material not found: {self.mat_name}')
            return {'CANCELLED'}

        fp = self.filepath
        if not os.path.exists(fp):
            self.report({'ERROR'}, f'File not found: {fp}')
            return {'CANCELLED'}

        # Find or create the texture node for this type
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Find BSDF
        bsdf = None
        for n in nodes:
            if n.type == 'BSDF_PRINCIPLED':
                bsdf = n
                break
        if not bsdf:
            self.report({'ERROR'}, 'No Principled BSDF found')
            return {'CANCELLED'}

        # Find existing texture node with this label/type
        tex_node = None
        for n in nodes:
            if n.type == 'TEX_IMAGE' and self.tex_type in (n.label or ''):
                tex_node = n
                break

        if not tex_node:
            # Create new node
            tex_node = nodes.new('ShaderNodeTexImage')
            tex_node.location = (-600, -300)

        # Load image
        img_name = f"{mat.name}_{self.tex_type}"
        img = load_dds(fp, img_name)
        if not img:
            self.report({'ERROR'}, f'Failed to load: {fp}')
            return {'CANCELLED'}

        tex_node.image = img
        tex_node.label = f"{self.tex_type}: {os.path.basename(fp)}"

        # Set color space
        if self.tex_type in ('diffuse', 'fx'):
            img.colorspace_settings.name = 'sRGB'
        else:
            img.colorspace_settings.name = 'Non-Color'

        # Connect to BSDF
        # First disconnect existing connections of this type
        for link in list(links):
            if link.from_node == tex_node:
                links.remove(link)

        if self.tex_type == 'diffuse':
            links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
        elif self.tex_type == 'normal':
            # Find or create Normal Map node
            nm_node = None
            for n in nodes:
                if n.type == 'NORMAL_MAP':
                    nm_node = n
                    break
            if not nm_node:
                nm_node = nodes.new('ShaderNodeNormalMap')
                nm_node.location = (-300, -300)
            links.new(tex_node.outputs['Color'], nm_node.inputs['Color'])
            links.new(nm_node.outputs['Normal'], bsdf.inputs['Normal'])
        elif self.tex_type == 'lightmap':
            if 'Specular IOR Level' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Specular IOR Level'])
            elif 'Specular' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Specular'])
        elif self.tex_type == 'fx':
            if 'Emission Color' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Emission Color'])
            elif 'Emission' in bsdf.inputs:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Emission'])
        elif self.tex_type == 'alpha':
            links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
            mat.blend_method = 'CLIP'

        self.report({'INFO'}, f'Applied {self.tex_type}: {os.path.basename(fp)}')
        return {'FINISHED'}


class MIGOTO_PT_texture_manager(bpy.types.Panel):
    """贴图管理面板 - 查看和修改所有贴图槽位"""
    bl_label = "贴图管理 / Texture Manager"
    bl_idname = "MIGOTO_PT_texture_manager"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if not obj or obj.type != 'MESH':
            layout.label(text='请选中一个网格对象 / Select a mesh object')
            return

        if not obj.active_material:
            layout.label(text='对象无材质 / No material')
            return

        mat = obj.active_material
        layout.label(text=f'材质: {mat.name}', icon='MATERIAL')

        if not mat.use_nodes:
            layout.label(text='材质未使用节点 / Material has no nodes')
            return

        # Find all texture nodes and their connections
        bsdf = None
        for n in mat.node_tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                bsdf = n
                break

        if not bsdf:
            layout.label(text='无 Principled BSDF')
            return

        # Show each texture node with its type and a change button
        tex_nodes = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE']
        if not tex_nodes:
            layout.label(text='无贴图节点 / No texture nodes')
            return

        for tex_node in tex_nodes:
            box = layout.box()
            row = box.row()

            # Texture type (from label or detect from connections)
            tex_type = 'unknown'
            if tex_node.label:
                # Extract type from label like "diffuse: filename.dds"
                label_parts = tex_node.label.split(':')
                tex_type = label_parts[0].strip()

            # Image name
            img_name = tex_node.image.name if tex_node.image else 'None'
            row.label(text=f'{tex_type}: {img_name}', icon='IMAGE_DATA')

            # Alpha toggle button
            if tex_node.image:
                alpha_mode = tex_node.image.alpha_mode
                alpha_icon = 'IMAGE_ALPHA' if alpha_mode != 'NONE' else 'CHECKBOX_DEHLT'
                op = row.operator('migoto.toggle_image_alpha', text='', icon=alpha_icon)
                op.image_name = tex_node.image.name

            # Change button
            op = row.operator('migoto.change_tex_slot', text='', icon='FILE_FOLDER')
            op.mat_name = mat.name
            op.tex_type = tex_type

            # Show connected inputs
            connected = []
            for link in mat.node_tree.links:
                if link.from_node == tex_node:
                    connected.append(f'→ {link.to_socket.name}')
            if connected:
                box.label(text=f'连接: {", ".join(connected)}', icon='LINKED')

        # Quick add texture buttons
        layout.separator()
        layout.label(text='快速添加 / Quick Add:', icon='ADD')
        row = layout.row(align=True)
        for tex_type in ['diffuse', 'normal', 'lightmap', 'fx', 'alpha']:
            op = row.operator('migoto.change_tex_slot', text=tex_type.capitalize())
            op.mat_name = mat.name
            op.tex_type = tex_type


class MIGOTO_OT_toggle_game_var(bpy.types.Operator):
    """切换游戏变量值 / Toggle game variable value"""
    bl_idname = "migoto.toggle_game_var"
    bl_label = "Toggle"
    bl_options = {'REGISTER', 'UNDO'}

    var_name: bpy.props.StringProperty()
    value: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_game_toggles'):
            return {'CANCELLED'}
        toggles = scene.migoto_game_toggles

        # Update the variable value
        for t in toggles.toggles:
            if t.var == self.var_name:
                t.value = self.value
                break

        # Build current toggle values dict
        toggle_values = {}
        for t in toggles.toggles:
            toggle_values[t.var] = t.value

        # Evaluate all objects with conditions and show/hide
        shown = 0
        hidden = 0
        for obj in bpy.data.objects:
            if obj.type != 'MESH':
                continue
            cond_str = obj.get('migoto_condition', None)
            if not cond_str:
                continue

            cond_parts = parse_condition(cond_str)
            if not cond_parts:
                continue

            # Check if this condition involves our changed variable
            involved = any(var == self.var_name for var, _, _ in cond_parts)
            if not involved:
                continue

            # Evaluate condition
            should_show = evaluate_condition(cond_parts, toggle_values)
            if should_show and obj.hide_viewport:
                obj.hide_viewport = False
                obj.hide_render = False
                shown += 1
            elif not should_show and not obj.hide_viewport:
                obj.hide_viewport = True
                obj.hide_render = True
                hidden += 1

        if shown or hidden:
            print(f"  [Migoto] ${self.var_name} = {self.value}: showed {shown}, hid {hidden}")

        return {'FINISHED'}


class MIGOTO_OT_reset_game_toggles(bpy.types.Operator):
    """重置所有开关为默认值 / Reset all toggles to defaults"""
    bl_idname = "migoto.reset_game_toggles"
    bl_label = "Reset All"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_game_toggles'):
            return {'CANCELLED'}
        for t in scene.migoto_game_toggles.toggles:
            t.value = t.default
        self.report({'INFO'}, '已重置所有开关 / Reset all toggles')
        return {'FINISHED'}


class MIGOTO_OT_load_game_toggles(bpy.types.Operator):
    """从选中对象的INI加载游戏开关 / Load game toggles from selected object's INI"""
    bl_idname = "migoto.load_game_toggles"
    bl_label = "Load Toggles"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        # Find INI from selected object's collection
        ini_path = None
        mod_name = None
        obj = context.active_object
        if obj:
            for coll in obj.users_collection:
                if 'migoto_ini_dir' in coll:
                    mod_dir = coll['migoto_ini_dir']
                    mod_name = coll.name
                    # Find INI in this directory
                    for root, dirs, files in os.walk(mod_dir):
                        for f in files:
                            if f.lower().endswith('.ini'):
                                ini_path = os.path.join(root, f)
                                break
                        if ini_path:
                            break
                    break

        # Fallback: find any migoto collection
        if not ini_path:
            for coll in bpy.data.collections:
                if 'migoto_ini_dir' in coll:
                    mod_dir = coll['migoto_ini_dir']
                    mod_name = coll.name
                    for root, dirs, files in os.walk(mod_dir):
                        for f in files:
                            if f.lower().endswith('.ini'):
                                ini_path = os.path.join(root, f)
                                break
                        if ini_path:
                            break
                    break

        if not ini_path:
            self.report({'ERROR'}, '未找到INI文件')
            return {'CANCELLED'}

        toggles = parse_ini_toggles(ini_path)
        if not toggles:
            self.report({'INFO'}, 'INI中未找到游戏开关')
            return {'CANCELLED'}

        if not hasattr(scene, 'migoto_game_toggles'):
            scene.migoto_game_toggles = bpy.props.PointerProperty(type=MIGOTO_PG_game_toggles)
        gt = scene.migoto_game_toggles
        gt.toggles.clear()

        for t_data in toggles:
            item = gt.toggles.add()
            item.name = t_data['name']
            item.var = t_data['var']
            item.value = t_data['default']
            item.default = t_data['default']
            item.max_val = t_data.get('max', 1)

        # Store INI path on collection
        for coll in bpy.data.collections:
            if 'migoto_ini_dir' in coll and coll.name == mod_name:
                coll['migoto_ini_path'] = ini_path
                break

        self.report({'INFO'}, f'[{mod_name}] 加载 {len(toggles)} 个开关')
        return {'FINISHED'}


class MIGOTO_PT_game_toggles(bpy.types.Panel):
    """游戏 MOD 开关面板"""
    bl_label = "游戏开关 / Game Toggles"
    bl_idname = "MIGOTO_PT_game_toggles"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Show selected object's MOD name
        obj = context.active_object
        mod_name = None
        if obj:
            for coll in obj.users_collection:
                if 'migoto_ini_dir' in coll:
                    mod_name = coll.name
                    break

        row = layout.row(align=True)
        row.operator('migoto.load_game_toggles', text='加载开关 / Load Toggles', icon='IMPORT')
        row.operator('migoto.reset_game_toggles', text='重置默认 / Reset', icon='LOOP_BACK')
        if mod_name:
            layout.label(text=f'MOD: {mod_name}', icon='OUTLINER_COLLECTION')
        layout.separator()

        if not hasattr(scene, 'migoto_game_toggles'):
            layout.label(text='选中对象后点击上方按钮加载开关')
            return

        gt = scene.migoto_game_toggles
        if not gt.toggles:
            layout.label(text='未加载开关 / No toggles loaded')
            return

        # Draw toggles
        for t in gt.toggles:
            row = layout.row(align=True)
            row.label(text=t.name)
            if t.max_val <= 1:
                icon = 'CHECKBOX_HLT' if t.value != 0 else 'CHECKBOX_DEHLT'
                op = row.operator('migoto.toggle_game_var', text='', icon=icon)
                op.var_name = t.var
                op.value = 0 if t.value != 0 else 1
            else:
                op = row.operator('migoto.toggle_game_var', text=str(t.value))
                op.var_name = t.var
                op.value = (t.value + 1) % (t.max_val + 1)


class MIGOTO_OT_auto_fill_textures(bpy.types.Operator):
    """自动填充贴图（从INI解析） / Auto-fill textures from INI"""
    bl_idname = "migoto.auto_fill_textures"
    bl_label = "Auto Fill Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Find the mod's INI and texture assignments
        mod_dir = None
        for coll in bpy.data.collections:
            if 'migoto_ini_dir' in coll:
                mod_dir = coll['migoto_ini_dir']
                break

        if not mod_dir:
            self.report({'ERROR'}, '未找到模型目录')
            return {'CANCELLED'}

        # Find INI file
        ini_path = None
        for root, dirs, files in os.walk(mod_dir):
            for f in files:
                if f.lower().endswith('.ini'):
                    ini_path = os.path.join(root, f)
                    break
            if ini_path:
                break

        if not ini_path:
            self.report({'ERROR'}, '未找到INI文件')
            return {'CANCELLED'}

        # Parse texture assignments from INI
        from collections import defaultdict
        tex_resources = {}  # resource_name -> filename
        current = None
        with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                s = line.strip()
                if s.startswith('[') and s.endswith(']'):
                    current = s[1:-1]
                    continue
                if not current or '=' not in s:
                    continue
                key, val = s.split('=', 1)
                key, val = key.strip(), val.strip()
                if current.startswith('Resource') and key == 'filename':
                    tex_resources[current] = val

        # Find texture files
        applied = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH' or not obj.active_material:
                continue
            mat = obj.active_material
            if not mat.use_nodes:
                continue

            # Try to find matching textures from INI resources
            obj_name_lower = obj.name.lower()
            for rname, fname in tex_resources.items():
                fl = fname.lower()
                if not any(ext in fl for ext in ['.dds', '.png', '.jpg']):
                    continue

                # Try to match by name similarity
                tex_type = classify_texture(fname)
                if tex_type == 'unknown':
                    continue

                # Check if this texture already exists in material
                already_exists = False
                for n in mat.node_tree.nodes:
                    if n.type == 'TEX_IMAGE' and n.label and tex_type in n.label:
                        already_exists = True
                        break
                if already_exists:
                    continue

                # Try to find the file
                fp = os.path.join(mod_dir, fname)
                if not os.path.exists(fp):
                    # Try subdirectories
                    for root, dirs, files in os.walk(mod_dir):
                        for f in files:
                            if f == os.path.basename(fname):
                                fp = os.path.join(root, f)
                                break
                        if os.path.exists(fp):
                            break

                if os.path.exists(fp):
                    # Apply this texture
                    tex_assign = {tex_type: fp}
                    assign_textures_to_material(mat, tex_assign, name_prefix=mat.name)
                    applied += 1

        self.report({'INFO'}, f'已应用 {applied} 张贴图 / Applied {applied} textures')
        return {'FINISHED'}


class MIGOTO_OT_rename_vertex_group(bpy.types.Operator):
    """重命名顶点组 / Rename vertex group"""
    bl_idname = "migoto.rename_vertex_group"
    bl_label = "Rename"
    bl_options = {'REGISTER', 'UNDO'}

    old_name: bpy.props.StringProperty()
    new_name: bpy.props.StringProperty(name="New Name")

    def invoke(self, context, event):
        self.new_name = self.old_name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        vg = obj.vertex_groups.get(self.old_name)
        if vg:
            vg.name = self.new_name
        return {'FINISHED'}


class MIGOTO_OT_show_vertex_weight(bpy.types.Operator):
    """显示顶点组权重（进入权重绘制模式） / Show vertex weights (enter weight paint)"""
    bl_idname = "migoto.show_vertex_weight"
    bl_label = "Show Weight"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中网格对象')
            return {'CANCELLED'}

        # Set active vertex group
        vg = obj.vertex_groups.get(self.group_name)
        if not vg:
            self.report({'WARNING'}, f'顶点组不存在: {self.group_name}')
            return {'CANCELLED'}
        obj.vertex_groups.active_index = vg.index

        # Enter weight paint mode to visualize weights
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')

        self.report({'INFO'}, f'显示权重: {self.group_name}')
        return {'FINISHED'}


class MIGOTO_OT_exit_weight_paint(bpy.types.Operator):
    """退出权重绘制模式 / Exit weight paint mode"""
    bl_idname = "migoto.exit_weight_paint"
    bl_label = "退出权重绘制 / Exit Weight Paint"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}


class MIGOTO_OT_generate_skeleton(bpy.types.Operator):
    """[实验性] 从顶点组权重自动生成骨骼 / [Experimental] Auto-generate skeleton"""
    bl_idname = "migoto.generate_skeleton"
    bl_label = "生成骨骼(实验性) / Generate Skeleton (Experimental)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import mathutils

        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中网格对象')
            return {'CANCELLED'}
        if not obj.vertex_groups:
            self.report({'ERROR'}, '对象没有顶点组权重')
            return {'CANCELLED'}

        mesh = obj.data

        # Calculate bone positions from vertex weights
        bone_positions = {}
        bone_weights_sum = {}

        for vi, vert in enumerate(mesh.vertices):
            for g in vert.groups:
                vg_idx = g.group
                weight = g.weight
                if weight > 0.01:
                    if vg_idx not in bone_positions:
                        bone_positions[vg_idx] = [0.0, 0.0, 0.0]
                        bone_weights_sum[vg_idx] = 0.0
                    co = vert.co
                    bone_positions[vg_idx][0] += co.x * weight
                    bone_positions[vg_idx][1] += co.y * weight
                    bone_positions[vg_idx][2] += co.z * weight
                    bone_weights_sum[vg_idx] += weight

        bone_centers = {}
        for vg_idx in bone_positions:
            tw = bone_weights_sum[vg_idx]
            if tw > 0:
                p = bone_positions[vg_idx]
                bone_centers[vg_idx] = mathutils.Vector((p[0]/tw, p[1]/tw, p[2]/tw))

        if not bone_centers:
            self.report({'ERROR'}, '无法计算骨骼位置')
            return {'CANCELLED'}

        armature = bpy.data.armatures.new(name=f'{obj.name}_Armature')
        arm_obj = bpy.data.objects.new(f'{obj.name}_Armature', armature)
        context.scene.collection.objects.link(arm_obj)

        context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode='EDIT')

        sorted_bones = sorted(bone_centers.items(), key=lambda x: -x[1].z)
        edit_bones = {}
        for vg_idx, center in sorted_bones:
            vg = obj.vertex_groups[vg_idx]
            bone = armature.edit_bones.new(vg.name)
            bone.head = center
            bone.tail = center + mathutils.Vector((0, 0, 0.05))
            edit_bones[vg_idx] = bone

        bone_list = list(edit_bones.items())
        for i, (vg_idx, bone) in enumerate(bone_list):
            best_parent = None
            best_dist = float('inf')
            head = bone.head
            for j, (pvg_idx, p_bone) in enumerate(bone_list):
                if i == j: continue
                dist = (head - p_bone.head).length
                if dist < best_dist and dist > 0.001:
                    if p_bone.head.z >= head.z - 0.02:
                        best_dist = dist
                        best_parent = p_bone
            if best_parent and best_dist < 0.3:
                bone.parent = best_parent
                if (bone.head - best_parent.head).length > 0.01:
                    best_parent.tail = bone.head

        bpy.ops.object.mode_set(mode='OBJECT')
        obj.select_set(True)
        arm_obj.select_set(True)
        context.view_layer.objects.active = arm_obj
        bpy.ops.object.parent_set(type='ARMATURE_NAME')
        context.view_layer.objects.active = obj
        obj.select_set(True)
        arm_obj.select_set(False)

        self.report({'INFO'}, f'生成 {len(edit_bones)} 根骨骼（实验性）')
        return {'FINISHED'}


class MIGOTO_OT_bind_bone_to_group(bpy.types.Operator):
    """将选中的骨骼名绑定到选中的顶点组（重命名顶点组以匹配骨骼名） / Bind selected bone name to selected vertex group"""
    bl_idname = "migoto.bind_bone_to_group"
    bl_label = "绑定骨骼到顶点组 / Bind Bone to Group"
    bl_options = {'REGISTER', 'UNDO'}

    bone_name: bpy.props.StringProperty(name="骨骼名 / Bone Name")
    group_name: bpy.props.StringProperty(name="顶点组名 / Vertex Group")

    def invoke(self, context, event):
        # Pre-fill with current active bone and vertex group
        obj = context.active_object
        arm = None

        # Find armature
        for o in context.selected_objects:
            if o.type == 'ARMATURE':
                arm = o
                break

        if arm and arm.data.bones.active:
            self.bone_name = arm.data.bones.active.name
        if obj and obj.type == 'MESH' and obj.vertex_groups.active:
            self.group_name = obj.vertex_groups.active.name

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中网格对象')
            return {'CANCELLED'}

        vg = obj.vertex_groups.get(self.group_name)
        if not vg:
            self.report({'ERROR'}, f'顶点组不存在: {self.group_name}')
            return {'CANCELLED'}

        old_name = vg.name
        vg.name = self.bone_name
        self.report({'INFO'}, f'"{old_name}" → "{self.bone_name}"')
        return {'FINISHED'}


class MIGOTO_OT_bind_selected_bone(bpy.types.Operator):
    """选中骨骼和顶点组后一键绑定并自动绑定骨架 / Quick bind and auto-parent to armature"""
    bl_idname = "migoto.bind_selected_bone"
    bl_label = "一键绑定 / Quick Bind"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中网格对象')
            return {'CANCELLED'}

        arm = None
        for o in context.selected_objects:
            if o.type == 'ARMATURE':
                arm = o
                break
        if not arm:
            self.report({'ERROR'}, '请同时选中一个骨架')
            return {'CANCELLED'}

        bone = arm.data.bones.active
        vg = obj.vertex_groups.active
        if not bone:
            self.report({'ERROR'}, '请在骨架中选中一根骨骼')
            return {'CANCELLED'}
        if not vg:
            self.report({'ERROR'}, '请在顶点组中选中一个组')
            return {'CANCELLED'}

        old_name = vg.name
        vg.name = bone.name

        # Auto-parent mesh to armature if not already parented
        self._auto_parent(obj, arm, context)

        self.report({'INFO'}, f'"{old_name}" → "{bone.name}"')
        return {'FINISHED'}

    def _auto_parent(self, obj, arm, context):
        """Auto-parent mesh to armature with Armature modifier."""
        # Check if already has armature modifier
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                return
        # Add armature modifier
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = arm
        # Set parent
        obj.parent = arm
        print(f"  [Migoto] Auto-parented {obj.name} to {arm.name}")


class MIGOTO_OT_auto_bind_by_position(bpy.types.Operator):
    """根据位置自动匹配骨骼和顶点组 / Auto-bind bones to vertex groups by position"""
    bl_idname = "migoto.auto_bind_by_position"
    bl_label = "自动匹配 / Auto Match"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import mathutils

        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, '请先选中网格对象')
            return {'CANCELLED'}

        arm = None
        for o in context.selected_objects:
            if o.type == 'ARMATURE':
                arm = o
                break
        if not arm:
            self.report({'ERROR'}, '请同时选中一个骨架')
            return {'CANCELLED'}

        mesh = obj.data

        # Calculate vertex group centers
        vg_centers = {}
        vg_weights = {}
        for vi, vert in enumerate(mesh.vertices):
            for g in vert.groups:
                if g.weight > 0.01:
                    vg_idx = g.group
                    if vg_idx not in vg_centers:
                        vg_centers[vg_idx] = [0.0, 0.0, 0.0]
                        vg_weights[vg_idx] = 0.0
                    co = vert.co
                    vg_centers[vg_idx][0] += co.x * g.weight
                    vg_centers[vg_idx][1] += co.y * g.weight
                    vg_centers[vg_idx][2] += co.z * g.weight
                    vg_weights[vg_idx] += g.weight

        vg_vectors = {}
        for vg_idx, pos in vg_centers.items():
            tw = vg_weights.get(vg_idx, 0)
            if tw > 0:
                vg_vectors[vg_idx] = mathutils.Vector((pos[0]/tw, pos[1]/tw, pos[2]/tw))

        # Get bone centers (in world space, transform to mesh local space)
        bones = [b for b in arm.data.bones]
        bone_vectors = {}
        arm_mat = arm.matrix_world.inverted() @ obj.matrix_world
        for bone in bones:
            center = (bone.head_local + bone.tail_local) / 2
            bone_vectors[bone.name] = center

        # Match each vertex group to nearest bone
        matched = 0
        used_bones = set()
        for vg_idx, vg_vec in vg_vectors.items():
            vg = obj.vertex_groups[vg_idx]
            best_bone = None
            best_dist = float('inf')
            for bone_name, bone_vec in bone_vectors.items():
                if bone_name in used_bones:
                    continue
                dist = (vg_vec - bone_vec).length
                if dist < best_dist:
                    best_dist = dist
                    best_bone = bone_name
            if best_bone and best_dist < 0.5:  # Max 50cm distance
                old_name = vg.name
                vg.name = best_bone
                used_bones.add(best_bone)
                matched += 1
                print(f"  [Migoto] {old_name} -> {best_bone} (dist={best_dist:.4f})")

        # Auto-parent mesh to armature if not already parented
        has_armature = any(mod.type == 'ARMATURE' for mod in obj.modifiers)
        if not has_armature and matched > 0:
            mod = obj.modifiers.new(name='Armature', type='ARMATURE')
            mod.object = arm
            obj.parent = arm
            print(f"  [Migoto] Auto-parented {obj.name} to {arm.name}")

        self.report({'INFO'}, f'匹配 {matched} 个顶点组到骨骼')
        return {'FINISHED'}


class MIGOTO_OT_import_pmx_skeleton(bpy.types.Operator):
    """从MMD tools导入的模型中提取骨架（先用MMD tools导入PMX，再点此按钮） / Extract skeleton from MMD-imported model"""
    bl_idname = "migoto.import_pmx_skeleton"
    bl_label = "提取MMD骨架 / Extract MMD Skeleton"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Find all armatures in scene
        armatures = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE']
        if not armatures:
            self.report({'ERROR'}, '场景中没有骨架，请先用MMD tools导入PMX')
            return {'CANCELLED'}

        # Use active object if it's an armature, otherwise use the last one
        arm = None
        if context.active_object and context.active_object.type == 'ARMATURE':
            arm = context.active_object
        else:
            arm = armatures[-1]

        # Collect MOD mesh names (from migoto collections) - don't delete these
        mod_mesh_names = set()
        for coll in bpy.data.collections:
            if 'migoto_ini_dir' in coll:
                for obj in coll.objects:
                    if obj.type == 'MESH':
                        mod_mesh_names.add(obj.name)

        # Delete only non-MOD meshes (MMD imported meshes)
        deleted = 0
        for obj in list(bpy.data.objects):
            if obj.type == 'MESH' and obj.name not in mod_mesh_names:
                bpy.data.objects.remove(obj, do_unlink=True)
                deleted += 1

        # Select the armature
        bpy.ops.object.select_all(action='DESELECT')
        arm.select_set(True)
        context.view_layer.objects.active = arm

        self.report({'INFO'}, f'提取骨架: {arm.name}，删除 {deleted} 个MMD网格')
        return {'FINISHED'}

        def read_str(off, enc):
            l = struct.unpack_from('<I', data, off)[0]
            return data[off+4:off+4+l].decode('utf-16-le' if enc == 0 else 'utf-8', errors='ignore'), off + 4 + l

        def read_int(off, sz):
            if sz == 1: return data[off], off + 1
            elif sz == 2: return struct.unpack_from('<h', data, off)[0], off + 2
            elif sz == 4: return struct.unpack_from('<i', data, off)[0], off + 4
            return 0, off

        off = 9
        gcount = data[8]
        g = [data[off + i] for i in range(min(gcount, 8))]
        off += gcount

        enc = g[0]
        extra_uv = g[1] if len(g) > 1 else 0
        vidx = g[2] if len(g) > 2 else 2
        tidx = g[3] if len(g) > 3 else 2
        midx = g[4] if len(g) > 4 else 2
        bidx = g[5] if len(g) > 5 else 2
        vbase = 32 + extra_uv * 16

        # Skip header text
        for _ in range(4): _, off = read_str(off, enc)

        # Skip vertices
        vc = struct.unpack_from('<I', data, off)[0]; off += 4
        for _ in range(vc):
            off += vbase
            wt = data[off]; off += 1
            if wt == 0: off += bidx
            elif wt == 1: off += bidx*2 + 4
            elif wt == 2: off += bidx*4 + 4
            elif wt == 3: off += bidx*2 + 40
            elif wt == 4: off += bidx*4 + 4

        # Skip faces
        fc = struct.unpack_from('<I', data, off)[0]; off += 4
        off += fc * vidx

        # Skip textures
        tc = struct.unpack_from('<I', data, off)[0]; off += 4
        for _ in range(tc):
            l = struct.unpack_from('<I', data, off)[0]; off += 4 + l

        # Skip materials
        mc = struct.unpack_from('<I', data, off)[0]; off += 4
        for _ in range(mc):
            for _ in range(2): _, off = read_str(off, enc)
            off += 16 + 12 + 4 + 1 + 16 + 4  # diffuse+specular+ambient+flag+edge+edgesize
            off += tidx + tidx + midx  # tex + sphtex + toon
            _, off = read_str(off, enc)  # comment
            off += 4  # index count

        # Parse bones
        bc = struct.unpack_from('<I', data, off)[0]; off += 4
        bones = []
        for _ in range(bc):
            name_jp, off = read_str(off, enc)
            name_en, off = read_str(off, enc)
            x, y, z = struct.unpack_from('<3f', data, off); off += 12
            parent, off = read_int(off, 2)
            off += 4  # layer
            flags = struct.unpack_from('<H', data, off)[0]; off += 2
            off += bidx if (flags & 1) else 12
            if flags & 8: off += bidx + 4
            if flags & 16: off += 12
            if flags & 32: off += 24
            if flags & 64: off += vidx
            if flags & 512:
                off += bidx + 6
                ikc = struct.unpack_from('<I', data, off)[0]; off += 4
                for _ in range(ikc):
                    off += bidx
                    if data[offset] if False else data[off]: off += 25
                    else: off += 1
            bones.append({'name': name_jp or name_en, 'position': (x, y, z), 'parent': parent})
        return bones


class MIGOTO_OT_select_bone(bpy.types.Operator):
    """选中骨骼并聚焦 / Select bone and focus"""
    bl_idname = "migoto.select_bone"
    bl_label = "Select Bone"
    bl_options = {'REGISTER'}

    bone_name: bpy.props.StringProperty()
    armature_name: bpy.props.StringProperty()

    def execute(self, context):
        arm = bpy.data.objects.get(self.armature_name)
        if not arm or arm.type != 'ARMATURE':
            return {'CANCELLED'}
        context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.select_all(action='DESELECT')
        if self.bone_name in arm.pose.bones:
            arm.pose.bones[self.bone_name].bone.select = True
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    override = context.copy()
                    override['area'] = area
                    override['region'] = area.regions[-1]
                    try:
                        with context.temp_override(**override):
                            bpy.ops.view3d.view_selected(use_all_regions=False)
                    except Exception:
                        pass
                    break
        return {'FINISHED'}


class MIGOTO_OT_delete_bone(bpy.types.Operator):
    """删除骨骼 / Delete bone"""
    bl_idname = "migoto.delete_bone"
    bl_label = "Delete Bone"
    bl_options = {'REGISTER', 'UNDO'}

    bone_name: bpy.props.StringProperty()
    armature_name: bpy.props.StringProperty()

    def execute(self, context):
        arm = bpy.data.objects.get(self.armature_name)
        if not arm or arm.type != 'ARMATURE':
            return {'CANCELLED'}
        context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        if self.bone_name in arm.data.edit_bones:
            arm.data.edit_bones.remove(arm.data.edit_bones[self.bone_name])
        bpy.ops.object.mode_set(mode='OBJECT')
        self.report({'INFO'}, f'已删除: {self.bone_name}')
        return {'FINISHED'}


class MIGOTO_OT_delete_all_non_mmd(bpy.types.Operator):
    """删除所有非MMD骨骼 / Delete all non-MMD bones"""
    bl_idname = "migoto.delete_all_non_mmd"
    bl_label = "删除非MMD骨骼 / Delete Non-MMD Bones"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = context.active_object
        if not arm or arm.type != 'ARMATURE':
            for o in context.selected_objects:
                if o.type == 'ARMATURE':
                    arm = o
                    break
        if not arm:
            self.report({'ERROR'}, '请选中一个骨架')
            return {'CANCELLED'}

        mmd_names = {
            'センター', 'Center', '下半身', 'LowerBody', '上半身', 'UpperBody',
            '上半身2', 'UpperBody2', '首', 'Neck', '頭', 'Head',
            '左腕', 'LeftArm', '左ひじ', 'LeftElbow', '左手首', 'LeftWrist',
            '右腕', 'RightArm', '右ひじ', 'RightElbow', '右手首', 'RightWrist',
            '左足', 'LeftLeg', '左ひざ', 'LeftKnee', '左足首', 'LeftAnkle',
            '左つま先', 'LeftToe', '右足', 'RightLeg', '右ひざ', 'RightKnee',
            '右足首', 'RightAnkle', '右つま先', 'RightToe',
            '左親指', 'LeftThumb', '左人指', 'LeftIndex', '左中指', 'LeftMiddle',
            '左薬指', 'LeftRing', '左小指', 'LeftPinky',
            '右親指', 'RightThumb', '右人指', 'RightIndex', '右中指', 'RightMiddle',
            '右薬指', 'RightRing', '右小指', 'RightPinky',
            '左足ＩＫ', 'LeftFootIK', '右足ＩＫ', 'RightFootIK',
            '左肩', 'LeftShoulder', '右肩', 'RightShoulder',
            '左目', 'LeftEye', '右目', 'RightEye', '両目', 'BothEyes',
            '腰', 'Waist', '胸', 'Chest',
        }

        context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        to_delete = []
        for bone in arm.data.edit_bones:
            is_mmd = bone.name in mmd_names
            if not is_mmd:
                for mmd in mmd_names:
                    if mmd in bone.name and len(mmd) > 2:
                        is_mmd = True
                        break
            if not is_mmd:
                to_delete.append(bone)
        for bone in to_delete:
            arm.data.edit_bones.remove(bone)
        bpy.ops.object.mode_set(mode='OBJECT')
        self.report({'INFO'}, f'删除 {len(to_delete)} 个非MMD骨骼')
        return {'FINISHED'}


MMD_BONE_NAMES = {
    'センター', 'Center', '下半身', 'LowerBody', '上半身', 'UpperBody',
    '上半身2', 'UpperBody2', '首', 'Neck', '頭', 'Head',
    '左腕', 'LeftArm', '左ひじ', 'LeftElbow', '左手首', 'LeftWrist',
    '右腕', 'RightArm', '右ひじ', 'RightElbow', '右手首', 'RightWrist',
    '左足', 'LeftLeg', '左ひざ', 'LeftKnee', '左足首', 'LeftAnkle',
    '左つま先', 'LeftToe', '右足', 'RightLeg', '右ひざ', 'RightKnee',
    '右足首', 'RightAnkle', '右つま先', 'RightToe',
    '左親指', 'LeftThumb', '左人指', 'LeftIndex', '左中指', 'LeftMiddle',
    '左薬指', 'LeftRing', '左小指', 'LeftPinky',
    '右親指', 'RightThumb', '右人指', 'RightIndex', '右中指', 'RightMiddle',
    '右薬指', 'RightRing', '右小指', 'RightPinky',
    '左足ＩＫ', 'LeftFootIK', '右足ＩＫ', 'RightFootIK',
    '左肩', 'LeftShoulder', '右肩', 'RightShoulder',
    '左目', 'LeftEye', '右目', 'RightEye', '両目', 'BothEyes',
    '腰', 'Waist', '胸', 'Chest',
}


def is_mmd_bone(name):
    """Check if a bone name is MMD standard."""
    if name in MMD_BONE_NAMES:
        return True
    for mmd in MMD_BONE_NAMES:
        if mmd in name and len(mmd) > 2:
            return True
    return False


class MIGOTO_PT_bone_manager(bpy.types.Panel):
    """骨骼管理面板"""
    bl_label = "骨骼管理 / Bone Manager"
    bl_idname = "MIGOTO_PT_bone_manager"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        arm = None
        if context.active_object and context.active_object.type == 'ARMATURE':
            arm = context.active_object
        else:
            for o in context.selected_objects:
                if o.type == 'ARMATURE':
                    arm = o
                    break

        if not arm:
            layout.label(text='请选中一个骨架 / Select an armature')
            return

        layout.label(text=f'骨架: {arm.name} ({len(arm.data.bones)} 根骨骼)', icon='ARMATURE_DATA')
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator('migoto.delete_all_non_mmd', text='🗑️ 删除非MMD骨骼', icon='TRASH')
        layout.separator()

        mmd_bones = [b for b in arm.data.bones if is_mmd_bone(b.name)]
        other_bones = [b for b in arm.data.bones if not is_mmd_bone(b.name)]

        box = layout.box()
        box.label(text=f'MMD 标准骨骼 ({len(mmd_bones)})', icon='CHECKBOX_HLT')
        for bone in mmd_bones[:30]:
            row = box.row(align=True)
            op = row.operator('migoto.select_bone', text=bone.name, icon='BONE_DATA')
            op.bone_name = bone.name
            op.armature_name = arm.name
        if len(mmd_bones) > 30:
            box.label(text=f'... 还有 {len(mmd_bones) - 30} 根', icon='INFO')

        box = layout.box()
        box.label(text=f'其他骨骼 ({len(other_bones)}) — 可删除', icon='ERROR')
        for bone in other_bones[:30]:
            row = box.row(align=True)
            op = row.operator('migoto.select_bone', text=bone.name, icon='BONE_DATA')
            op.bone_name = bone.name
            op.armature_name = arm.name
            op = row.operator('migoto.delete_bone', text='', icon='TRASH')
            op.bone_name = bone.name
            op.armature_name = arm.name
        if len(other_bones) > 30:
            box.label(text=f'... 还有 {len(other_bones) - 30} 根', icon='INFO')


class MIGOTO_PT_vertex_groups(bpy.types.Panel):
    """顶点组权重面板"""
    bl_label = "顶点组 / Vertex Groups"
    bl_idname = "MIGOTO_PT_vertex_groups"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        # Bone binding tools - always visible
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator('migoto.bind_selected_bone', text='🦴 一键绑定 / Quick Bind', icon='LINKED')
        row.operator('migoto.bind_bone_to_group', text='✏️ 手动绑定 / Manual Bind', icon='GREASEPENCIL')
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator('migoto.auto_bind_by_position', text='🤖 自动匹配 / Auto Match', icon='MOD_VERTEX_WEIGHT')
        row.operator('migoto.generate_skeleton', text='⚡ 生成骨骼(实验性)', icon='ARMATURE_DATA')
        row.operator('migoto.import_pmx_skeleton', text='📥 提取MMD骨架', icon='ARMATURE_DATA')
        layout.separator()

        # Show exit button if in weight paint mode
        if context.mode == 'PAINT_WEIGHT':
            row = layout.row()
            row.scale_y = 1.5
            row.operator('migoto.exit_weight_paint', text='退出权重绘制 / Exit Weight Paint', icon='X')
            layout.separator()

        # Show vertex group list only for mesh objects
        if not obj or obj.type != 'MESH':
            if obj and obj.type == 'ARMATURE':
                layout.label(text='骨架已选中，可用上方按钮操作', icon='ARMATURE_DATA')
            else:
                layout.label(text='请选中网格对象或骨架', icon='QUESTION')
            return

        if not obj.vertex_groups:
            layout.label(text='无顶点组 / No vertex groups')
            return

        # List vertex groups
        for vg in obj.vertex_groups:
            row = layout.row(align=True)
            # Highlight active group
            is_active = (obj.vertex_groups.active_index == vg.index)
            icon = 'RADIOBUT_ON' if is_active else 'GROUP_VERTEX'
            # Click to show weight
            op = row.operator('migoto.show_vertex_weight', text=vg.name, icon=icon)
            op.group_name = vg.name
            # Rename button
            op = row.operator('migoto.rename_vertex_group', text='', icon='GREASEPENCIL')
            op.old_name = vg.name
            # Delete button
            op = row.operator('object.vertex_group_remove', text='', icon='X')

        # Bulk operations
        layout.separator()
        row = layout.row(align=True)
        row.operator('object.vertex_group_remove_all', text='删除全部 / Remove All', icon='TRASH')


classes = (
    MIGOTO_PG_texture_item,
    MIGOTO_PG_texture_state,
    MIGOTO_PG_game_toggle,
    MIGOTO_PG_game_toggles,
    MIGOTO_PG_mesh_group_item,
    MIGOTO_PG_mesh_group,
    MIGOTO_PG_mesh_groups,
    MIGOTO_OT_load_textures,
    MIGOTO_OT_apply_texture,
    MIGOTO_OT_apply_texture_from_list,
    MIGOTO_OT_select_texture,
    MIGOTO_OT_add_group,
    MIGOTO_OT_remove_group,
    MIGOTO_OT_add_obj_to_group,
    MIGOTO_OT_remove_obj_from_group,
    MIGOTO_OT_toggle_obj_visibility,
    MIGOTO_OT_highlight_obj,
    MIGOTO_OT_show_all_in_group,
    MIGOTO_OT_hide_all_in_group,
    MIGOTO_OT_rename_group,
    MIGOTO_OT_select_group,
    MIGOTO_OT_auto_group,
    MIGOTO_OT_export_textures,
    MIGOTO_OT_clear_textures,
    MIGOTO_OT_toggle_mod_textures,
    MIGOTO_OT_select_mod_texture,
    MIGOTO_OT_apply_mod_texture,
    MIGOTO_UL_mesh_group_items,
    MIGOTO_UL_mesh_groups,
    MIGOTO_UL_texture_list,
    MIGOTO_PT_mesh_groups,
    MIGOTO_PT_panel,
    MIGOTO_PT_texture_manager,
    MIGOTO_OT_change_tex_slot,
    MIGOTO_OT_toggle_image_alpha,
    MIGOTO_OT_auto_fill_textures,
    MIGOTO_OT_toggle_game_var,
    MIGOTO_OT_reset_game_toggles,
    MIGOTO_OT_load_game_toggles,
    MIGOTO_OT_rename_vertex_group,
    MIGOTO_OT_show_vertex_weight,
    MIGOTO_OT_exit_weight_paint,
    MIGOTO_OT_generate_skeleton,
    MIGOTO_OT_bind_bone_to_group,
    MIGOTO_OT_bind_selected_bone,
    MIGOTO_OT_auto_bind_by_position,
    MIGOTO_OT_import_pmx_skeleton,
    MIGOTO_OT_select_bone,
    MIGOTO_OT_delete_bone,
    MIGOTO_OT_delete_all_non_mmd,
    MIGOTO_PT_bone_manager,
    MIGOTO_PT_game_toggles,
    MIGOTO_PT_vertex_groups,
    IMPORT_OT_3dmigoto,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_fn)
    bpy.types.Scene.migoto_mesh_groups = bpy.props.PointerProperty(type=MIGOTO_PG_mesh_groups)
    bpy.types.Scene.migoto_textures = bpy.props.CollectionProperty(type=MIGOTO_PG_texture_item)
    bpy.types.Scene.migoto_tex_state = bpy.props.PointerProperty(type=MIGOTO_PG_texture_state)
    bpy.types.Scene.migoto_game_toggles = bpy.props.PointerProperty(type=MIGOTO_PG_game_toggles)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_fn)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.migoto_mesh_groups
    del bpy.types.Scene.migoto_textures
    del bpy.types.Scene.migoto_tex_state
    del bpy.types.Scene.migoto_game_toggles
    # Clean up preview collections
    for pcoll in _preview_collections.values():
        bpy.utils.previews.remove(pcoll)
    _preview_collections.clear()

if __name__ == "__main__":
    register()
