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
    "author": "OpenClaw",
    "version": (3, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > 3Dmigoto Model (.ini)",
    "description": "Import 3Dmigoto/XXMI game mod models",
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
    """Read XYZ positions with optional rotation and mirror."""
    import math
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride

    # Build rotation matrix from Euler angles (XYZ order)
    cx, sx = math.cos(rot_x), math.sin(rot_x)
    cy, sy = math.cos(rot_y), math.sin(rot_y)
    cz, sz = math.cos(rot_z), math.sin(rot_z)
    m00 = cx*cy;  m01 = cx*sy*sz - sx*cz;  m02 = cx*sy*cz + sx*sz
    m10 = sx*cy;  m11 = sx*sy*sz + cx*cz;  m12 = sx*sy*cz - cx*sz
    m20 = -sy;    m21 = cy*sz;              m22 = cy*cz

    # Mirror factors
    mx = -1.0 if mirror_x else 1.0
    my = -1.0 if mirror_y else 1.0
    mz = -1.0 if mirror_z else 1.0

    out = []
    for i in range(n):
        x, y, z = struct.unpack_from('<3f', data, i * stride)
        x *= mx; y *= my; z *= mz
        nx = m00*x + m01*y + m02*z
        ny = m10*x + m11*y + m12*z
        nz = m20*x + m21*y + m22*z
        out.append((nx, ny, nz))
    return out



def read_uvs(filepath, stride, hf_offset=None, uv_format="auto"):
    """Read UVs. Auto-detect format (half-float vs uint16 UNORM) and offset."""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride

    if hf_offset is not None:
        return [(u, 1.0 - v) for u, v in (struct.unpack_from('<ee', data, i * stride + hf_offset) for i in range(n))]

    # Manual format selection
    if uv_format != 'auto':
        fmt_map = {'hf0': ('hf',0), 'hf4': ('hf',4), 'u16_0': ('u16',0), 'u16_2': ('u16',2), 'u16_4': ('u16',4), 'f32_4': ('f32',4), 'f32_0': ('f32',0)}
        if uv_format in fmt_map:
            fmt, off = fmt_map[uv_format]
            print(f"  [Migoto] UV: manual format={fmt}, offset={off}")
            out = []
            for i in range(n):
                if fmt == 'hf':
                    u, v = struct.unpack_from('<ee', data, i * stride + off)
                elif fmt == 'u16':
                    u16, v16 = struct.unpack_from('<HH', data, i * stride + off)
                    u, v = u16 / 65535.0, v16 / 65535.0
                else:
                    u, v = struct.unpack_from('<2f', data, i * stride + off)
                out.append((u, 1.0 - v))
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

    best_fmt, best_off, best_score = 'hf', 0, -1
    for fmt, off in candidates:
        score = 0
        u_vals, v_vals = [], []
        sample = min(2000, n)
        for i in range(sample):
            try:
                if fmt == 'hf':
                    u, v = struct.unpack_from('<ee', data, i * stride + off)
                elif fmt == 'u16':
                    u16, v16 = struct.unpack_from('<HH', data, i * stride + off)
                    u, v = u16 / 65535.0, v16 / 65535.0
                else:  # f32
                    u, v = struct.unpack_from('<2f', data, i * stride + off)
                if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                    score += 1
                    u_vals.append(u)
                    v_vals.append(v)
            except:
                pass
        # Prefer formats with wide UV coverage (at least one axis > 50%)
        if len(u_vals) > 10:
            u_range = max(u_vals) - min(u_vals)
            v_range = max(v_vals) - min(v_vals)
            coverage = max(u_range, v_range)  # Best axis coverage
            # Bonus for both axes having range
            if u_range > 0.1 and v_range > 0.1:
                score = int(score * 1.5)
            # Penalty if both axes are narrow
            if coverage < 0.3:
                score = score // 5
        if score > best_score:
            best_score, best_fmt, best_off = score, fmt, off

    print(f"  [Migoto] UV: stride={stride}, format={best_fmt}, offset={best_off} ({best_score}/{min(2000, n)} valid)")

    out = []
    for i in range(n):
        if best_fmt == 'hf':
            u, v = struct.unpack_from('<ee', data, i * stride + best_off)
        elif best_fmt == 'u16':
            u16, v16 = struct.unpack_from('<HH', data, i * stride + best_off)
            u, v = u16 / 65535.0, v16 / 65535.0
        else:  # f32
            u, v = struct.unpack_from('<2f', data, i * stride + best_off)
        out.append((u, 1.0 - v))
    return out


def read_indices(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    return list(struct.unpack(f'<{len(data)//4}I', data))


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
                    except:
                        pass
    
    # Merge: for sections with Component in name, use comp_textures
    for section in list(section_textures.keys()):
        match = _re.search(r'Component(d+)', section)
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
                sections[current_section] = {'ib': None, 'vb0': None, 'vb1': None, 'vb2': None, 'draws': [], 'runs': [], 'textures': {}}
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
                except: pass
            elif key == 'format':
                resources[current_section]['format'] = val
            elif key == 'type':
                resources[current_section]['type'] = val

        if key == 'ib':
            sections[current_section]['ib'] = val if val.lower() != 'null' else None
        if key == 'vb0':
            sections[current_section]['vb0'] = val
        if key == 'vb1':
            sections[current_section]['vb1'] = val
        if key == 'vb2':
            sections[current_section]['vb2'] = val
        if key == 'run':
            sections[current_section]['runs'].append(val)

        # Capture texture references (multiple formats)
        if key.startswith('ps-t0') and val.lower().startswith('resource'):
            sections[current_section]['textures']['diffuse'] = val
        elif key.startswith('ps-t1') and val.lower().startswith('resource'):
            sections[current_section]['textures']['lightmap'] = val
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
                count = int(args[0])
                start = int(args[1]) if len(args) > 1 else 0
                base = int(args[2]) if len(args) > 2 else 0

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
    for sec_name, sec_data in sections.items():
        if not sec_data['draws']:
            continue

        ctx = caller_ctx.get(sec_name, {})
        eff_ib = sec_data['ib'] if sec_data['ib'] is not None else ctx.get('ib')
        eff_vb0 = sec_data['vb0'] if sec_data['vb0'] is not None else ctx.get('vb0')
        eff_vb1 = sec_data['vb1'] if sec_data['vb1'] is not None else ctx.get('vb1')
        eff_vb2 = sec_data.get('vb2') or ctx.get('vb2')
        eff_tex = {**ctx.get('textures', {}), **sec_data['textures']}

        for dc in sec_data['draws']:
            dc['ib_resource'] = eff_ib
            dc['vb0_resource'] = eff_vb0
            dc['vb1_resource'] = eff_vb1
            dc['vb2_resource'] = eff_vb2
            dc['textures'] = eff_tex.copy()
            # Propagate condition if not already set
            if 'condition' not in dc:
                dc['condition'] = None
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
                print(f"      candidate={candidate} exists={os.path.exists(candidate)}")
    
    return resolved


def load_mesh_data(resolved, draw_calls, mirror_x=False, mirror_y=False, mirror_z=False, rot_x=0, rot_y=0, rot_z=0, uv_format="auto"):
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

        indices = read_indices(resolved[ib_name]['path'])

        # Try explicit VB resources from draw calls first (WWMI format)
        vb_info = ib_vb_map.get(ib_name, {})
        pos_res = vb_info.get('vb0')
        uv_res = vb_info.get('vb2') or vb_info.get('vb1')  # WWMI uses vb2 for texcoord

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
            uvs = read_uvs(r['path'], r['stride'], uv_format=uv_format)

        mesh_data[ib_name] = {
            'positions': positions,
            'uvs': uvs,
            'indices': indices,
        }

        print(f"  IB: {ib_name} -> pos={pos_res} ({len(positions)} verts), uv={uv_res} ({len(uvs)} uvs), idx={len(indices)}")

    return mesh_data


def find_textures(ini_dir, resources, resolved):
    """Find diffuse textures from resolved resource paths."""
    textures = {}
    
    for rname, rdata in resources.items():
        if 'filename' not in rdata:
            continue
        
        fname = rdata['filename'].replace('\\', '/')
        fl = fname.lower()
        rl = rname.lower()
        
        # Skip non-texture resources
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
        
        # Classify texture by resource name and path
        if 'face' in rl or 'face' in fl or 'head' in fl:
            if 'diffuse' in rl or 'base' in fl or 'diffuse' in fl:
                k = 'face_diffuse2' if '2' in fl else 'face_diffuse1'
                textures[k] = fp
        elif 'hair' in rl or 'hair' in fl:
            if 'diffuse' in rl or 'base' in fl or 'diffuse' in fl:
                textures['hair_diffuse'] = fp
        elif 'body' in rl or 'body' in fl:
            if 'diffuse' in rl or 'base' in fl or 'diffuse' in fl:
                textures['body_diffuse'] = fp
    
    return textures


def load_dds(filepath, name):
    """Load texture into Blender. Let Blender handle DDS natively."""
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
        except:
            return None
    
    # For DDS files: try Blender's native DDS loader first
    try:
        img = bpy.data.images.load(filepath)
        img.name = name
        # Set color space for diffuse textures
        if 'Diffuse' in name or 'diffuse' in name:
            img.colorspace_settings.name = 'sRGB'
        else:
            img.colorspace_settings.name = 'Non-Color'
        return img
    except Exception as e:
        print(f"  [Migoto] Blender DDS load failed: {os.path.basename(filepath)}: {e}")
    
    # Fallback: convert DDS to PNG via Pillow (only if Blender can't load)
    try:
        from PIL import Image as PILImage
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix='migoto_tex_')
        png_name = os.path.splitext(os.path.basename(filepath))[0] + '.png'
        png_path = os.path.join(temp_dir, png_name)
        PILImage.open(filepath).save(png_path)
        img = bpy.data.images.load(png_path)
        img.name = name
        return img
    except:
        return None


# ============================================================
# Material & Mesh
# ============================================================

def make_material(name, diffuse=None, alpha=False):
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
    
    # Always create texture node
    t = nodes.new('ShaderNodeTexImage')
    t.location = (-300, 0)
    
    if diffuse:
        img = load_dds(diffuse, f"{name}_Diffuse")
        if img:
            t.image = img
            links.new(t.outputs['Color'], bsdf.inputs['Base Color'])
            if alpha:
                links.new(t.outputs['Alpha'], bsdf.inputs['Alpha'])
                mat.blend_method = 'CLIP'
    
    return mat



def safe_name(name):
    return re.sub(r'[^\w\-. ]', '_', name).strip('_')


def build_object(name, all_positions, all_uvs, triangles, material, collection):
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

    faces = []
    for i0, i1, i2 in triangles:
        if i0 in remap and i1 in remap and i2 in remap:
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
    for face_idx, (i0, i1, i2) in enumerate(faces):
        base = face_idx * 3
        uv_layer.data[base].uv = vert_uvs[i0]
        uv_layer.data[base + 1].uv = vert_uvs[i1]
        uv_layer.data[base + 2].uv = vert_uvs[i2]

    mesh.update()

    if material:
        obj.data.materials.append(material)
    for p in obj.data.polygons:
        p.use_smooth = True

    return obj, len(faces)


# ============================================================
# Import Operator
# ============================================================

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
    uv_format: bpy.props.EnumProperty(
        name="UV 格式 / UV Format",
        items=[
            ('auto', '自动检测 / Auto', '自动检测UV格式'),
            ('hf0', 'Half-float @0', ''),
            ('hf4', 'Half-float @4', ''),
            ('u16_0', 'uint16 @0', ''),
            ('u16_2', 'uint16 @2', ''),
            ('u16_4', 'uint16 @4', ''),
            ('f32_4', 'float32 @4', ''),
            ('f32_0', 'float32 @0', ''),
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
        ini_path = self.filepath
        
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

        draw_calls, resources = parse_ini_full(ini_path)
        if not draw_calls:
            self.report({'ERROR'}, "No draw calls found")
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

        # Load mesh data per IB
        mesh_data = load_mesh_data(resolved, draw_calls, mirror_x=self.mirror_x, mirror_y=self.mirror_y, mirror_z=self.mirror_z, rot_x=self.rot_x, rot_y=self.rot_y, rot_z=self.rot_z, uv_format=self.uv_format)

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
            match = _re.search(r'Component(d+)', sec)
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
            tex_ref = dc.get('textures', {}).get('diffuse') or dc.get('textures', {}).get('this')
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
        context.scene.collection.children.link(coll)

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
            materials[sec] = make_material(mat_name, diffuse=tex_path, alpha=is_hair)
            if tex_path:
                print(f"  [Migoto] Material {mat_name} -> {os.path.basename(tex_path)}")

        obj_count = 0
        print(f"  [Migoto] Creating meshes...")

        if self.split_parts:
            for dc in draw_calls:
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

                obj, cnt = build_object(dc['name'], positions, uvs, tris, mat, coll)
                if cnt > 0:
                    obj_count += 1
                    # Tag with variant condition
                    cond = dc.get('condition')
                    if cond:
                        obj['migoto_condition'] = cond
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
                    obj, cnt = build_object(f"{mod_name}_merged", positions, uvs, tris, mat, coll)
                    if cnt > 0:
                        obj_count += 1

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


class MIGOTO_OT_load_textures(bpy.types.Operator):
    """扫描目录加载贴图列表 / Scan directory for textures"""
    bl_idname = "migoto.load_textures"
    bl_label = "加载贴图列表 / Load Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        # Find mod directory
        mod_dir = None
        for coll in bpy.data.collections:
            if 'migoto_ini_dir' in coll:
                mod_dir = coll['migoto_ini_dir']
                break

        if not mod_dir or not os.path.isdir(mod_dir):
            self.report({'ERROR'}, '未找到模型目录')
            return {'CANCELLED'}

        if not hasattr(scene, 'migoto_textures'):
            scene.migoto_textures = bpy.props.CollectionProperty(type=MIGOTO_PG_texture_item)
        scene.migoto_textures.clear()

        # Clear old preview collection
        if 'default' in _preview_collections:
            bpy.utils.previews.remove(_preview_collections['default'])
        _preview_collections['default'] = bpy.utils.previews.new()
        pcoll = _preview_collections['default']

        # Scan mod directory + one level of subdirectories
        tex_files = []
        for f in os.listdir(mod_dir):
            fp = os.path.join(mod_dir, f)
            if os.path.isfile(fp) and f.lower().endswith(('.dds', '.png', '.jpg')):
                tex_files.append((fp, 'root'))
        for entry in os.listdir(mod_dir):
            sub = os.path.join(mod_dir, entry)
            if not os.path.isdir(sub):
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

            # Load preview icon
            load_fp = fp
            # For DDS, try to load directly; if preview fails, convert to temp PNG
            preview_loaded = False
            try:
                preview = pcoll.load(fp, fp, 'IMAGE')
                item.preview_icon = preview.icon_id
                preview_loaded = True
            except:
                pass
            
            if not preview_loaded and fp.lower().endswith('.dds'):
                try:
                    from PIL import Image as PILImage
                    import tempfile
                    temp_dir = tempfile.mkdtemp(prefix='migoto_tex_')
                    png_name = os.path.splitext(os.path.basename(fp))[0] + '.png'
                    png_path = os.path.join(temp_dir, png_name)
                    if not os.path.exists(png_path):
                        PILImage.open(fp).save(png_path)
                    preview = pcoll.load(fp, png_path, 'IMAGE')
                    item.preview_icon = preview.icon_id
                except:
                    item.preview_icon = 0

        self.report({'INFO'}, f'加载 {len(scene.migoto_textures)} 张贴图')
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
                temp_dir = tempfile.mkdtemp(prefix='migoto_tex_')
                png_name = os.path.splitext(os.path.basename(fp))[0] + '.png'
                png_path = os.path.join(temp_dir, png_name)
                PILImage.open(fp).save(png_path)
                fp = png_path
            except:
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

        # Apply directly (load_dds handles DDS conversion to temp)
        bpy.ops.migoto.apply_texture(filepath=item.filepath)
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

        # Find migoto collection
        for coll in bpy.data.collections:
            if 'migoto_ini_dir' not in coll:
                continue

            # Parse conditions: extract variable name from conditions like '$booba == 0'
            var_groups = {}  # var_name -> {value -> [obj_name, ...]}
            no_cond_objects = []

            for obj in coll.objects:
                if obj.type != 'MESH':
                    continue
                cond = obj.get('migoto_condition', None)
                if cond:
                    # Extract variable name: '$booba == 0' -> '$booba'
                    import re as _re
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

            mg.groups.clear()

            # Create a group for each variable
            for var_name, values in sorted(var_groups.items()):
                g = mg.groups.add()
                g.name = var_name
                for value, obj_names in sorted(values.items()):
                    for oname in obj_names:
                        item = g.items.add()
                        item.obj_name = oname
                # Show all by default (not mutually exclusive)
                for item in g.items:
                    obj = bpy.data.objects.get(item.obj_name)
                    if obj:
                        obj.hide_viewport = False
                        obj.hide_render = False

            # Group always-visible objects
            if no_cond_objects:
                g = mg.groups.add()
                g.name = '常显 / Always Visible'
                for oname in no_cond_objects:
                    item = g.items.add()
                    item.obj_name = oname

            self.report({'INFO'}, f'创建 {len(mg.groups)} 个分组 / Created {len(mg.groups)} groups')
            return {'FINISHED'}

        self.report({'INFO'}, '未找到导入模型 / No imported model found')
        return {'CANCELLED'}


class MIGOTO_PT_mesh_groups(bpy.types.Panel):
    """网格变体分组面板"""
    bl_label = "网格变体 / Mesh Variants"
    bl_idname = "MIGOTO_PT_mesh_groups"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '3Dmigoto'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Auto-group button
        row = layout.row()
        row.operator('migoto.auto_group', text='自动分组 / Auto Group', icon='FILTER')
        row.operator('migoto.add_group', text='添加分组 / Add Group', icon='ADD')

        if not hasattr(scene, 'migoto_mesh_groups'):
            return
        mg = scene.migoto_mesh_groups

        if not mg.groups:
            layout.label(text='无分组 / No groups')
            return

        for g_idx, group in enumerate(mg.groups):
            box = layout.box()

            # Group header
            row = box.row()
            row.label(text=group.name, icon='GROUP')
            op = row.operator('migoto.rename_group', text='', icon='GREASEPENCIL')
            op.group_index = g_idx
            op = row.operator('migoto.remove_group', text='', icon='X')
            op.index = g_idx

            # Add selected button
            op = box.operator('migoto.add_obj_to_group', text='添加选中 / Add Selected', icon='ADD')
            op.group_index = g_idx

            # List objects with independent checkboxes
            for i_idx, item in enumerate(group.items):
                row = box.row()
                obj = bpy.data.objects.get(item.obj_name)

                # Checkbox toggle
                is_visible = obj and not obj.hide_viewport
                icon = 'CHECKBOX_HLT' if is_visible else 'CHECKBOX_DEHLT'
                op = row.operator('migoto.toggle_obj_visibility', text=item.obj_name, icon=icon)
                op.obj_name = item.obj_name

                # Remove button
                op = row.operator('migoto.remove_obj_from_group', text='', icon='TRASH')
                op.group_index = g_idx
                op.item_index = i_idx

            # Show all / Hide all
            row = box.row(align=True)
            op = row.operator('migoto.show_all_in_group', text='全部显示 / Show All', icon='HIDE_OFF')
            op.group_index = g_idx
            op = row.operator('migoto.hide_all_in_group', text='全部隐藏 / Hide All', icon='HIDE_ON')
            op.group_index = g_idx


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

            # Load textures button
            box.operator('migoto.load_textures', text='加载贴图列表 / Load Textures', icon='FILE_FOLDER')

            # Auto-load if empty and mod_dir exists
            if not hasattr(scene, 'migoto_textures') or len(scene.migoto_textures) == 0:
                # Try to auto-load
                for coll in bpy.data.collections:
                    if 'migoto_ini_dir' in coll:
                        mod_dir = coll['migoto_ini_dir']
                        if os.path.isdir(mod_dir):
                            box.label(text='提示: 点击上方按钮加载贴图列表', icon='INFO')
                        break
                return

            # Show texture list grouped by category
            categories = {}
            for i, item in enumerate(scene.migoto_textures):
                cat = item.category or 'root'
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append((i, item))

            for cat, items in sorted(categories.items()):
                cat_box = box.box()
                cat_box.label(text=f'{cat} ({len(items)}张贴图)', icon='FILE_FOLDER')

                for idx, (i, item) in enumerate(items):
                    row = cat_box.row(align=True)

                    # Small preview icon
                    if item.preview_icon:
                        row.label(text='', icon_value=item.preview_icon)

                    # Check if currently applied
                    is_current = False
                    if obj.active_material and obj.active_material.use_nodes:
                        for node in obj.active_material.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                try:
                                    img_base = os.path.splitext(os.path.basename(bpy.path.abspath(node.image.filepath)))[0]
                                    tex_base = os.path.splitext(os.path.basename(item.filepath))[0]
                                    if img_base == tex_base:
                                        is_current = True
                                except:
                                    pass
                                break

                    # Click to apply
                    icon = 'RADIOBUT_ON' if is_current else 'IMAGE_DATA'
                    op = row.operator('migoto.apply_texture_from_list', text=item.name, icon=icon)
                    op.index = i
        else:
            layout.label(text='请选中一个网格对象')


classes = (
    MIGOTO_PG_texture_item,
    MIGOTO_PG_mesh_group_item,
    MIGOTO_PG_mesh_group,
    MIGOTO_PG_mesh_groups,
    MIGOTO_OT_load_textures,
    MIGOTO_OT_apply_texture,
    MIGOTO_OT_apply_texture_from_list,
    MIGOTO_OT_add_group,
    MIGOTO_OT_remove_group,
    MIGOTO_OT_add_obj_to_group,
    MIGOTO_OT_remove_obj_from_group,
    MIGOTO_OT_toggle_obj_visibility,
    MIGOTO_OT_show_all_in_group,
    MIGOTO_OT_hide_all_in_group,
    MIGOTO_OT_rename_group,
    MIGOTO_OT_auto_group,
    MIGOTO_OT_export_textures,
    MIGOTO_PT_mesh_groups,
    MIGOTO_PT_panel,
    IMPORT_OT_3dmigoto,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_fn)
    bpy.types.Scene.migoto_mesh_groups = bpy.props.PointerProperty(type=MIGOTO_PG_mesh_groups)
    bpy.types.Scene.migoto_textures = bpy.props.CollectionProperty(type=MIGOTO_PG_texture_item)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_fn)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.migoto_mesh_groups
    del bpy.types.Scene.migoto_textures
    # Clean up preview collections
    for pcoll in _preview_collections.values():
        bpy.utils.previews.remove(pcoll)
    _preview_collections.clear()

if __name__ == "__main__":
    register()
