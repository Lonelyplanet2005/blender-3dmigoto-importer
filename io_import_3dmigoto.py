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
    "version": (1, 2, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > 3Dmigoto Model (.ini)",
    "description": "Import 3Dmigoto/XXMI game mod models",
    "category": "Import-Export",
}

import bpy
import bmesh
import struct
import os
import re
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty


# ============================================================
# Binary Readers
# ============================================================

def read_positions(filepath, stride=40, rotate=True, mirror_x=False):
    """Read XYZ positions. If rotate=True, apply -90° X rotation for Blender."""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride
    out = []
    for i in range(n):
        x, y, z = struct.unpack_from('<3f', data, i * stride)
        if rotate:
            # -90° X: (x,y,z) -> (x, z, -y)
            if mirror_x:
                out.append((-x, z, -y))
            else:
                out.append((x, z, -y))
        else:
            if mirror_x:
                out.append((-x, y, z))
            else:
                out.append((x, y, z))
    return out


def read_uvs(filepath, stride, hf_offset=4):
    """Read UVs as half-floats at hf_offset. Flip V for Blender."""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // stride
    out = []
    for i in range(n):
        u, v = struct.unpack_from('<ee', data, i * stride + hf_offset)
        out.append((u, 1.0 - v))
    return out


def read_indices(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    return list(struct.unpack(f'<{len(data)//4}I', data))


# ============================================================
# INI Parser
# ============================================================

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
                sections[current_section] = {'ib': None, 'vb0': None, 'vb1': None, 'draws': [], 'runs': [], 'textures': {}}
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
        if key == 'run':
            sections[current_section]['runs'].append(val)

        # Capture texture references: Resource\ZZMI\Diffuse = ref ResourceXXX
        if 'diffuse' in key.lower() and val.lower().startswith('ref '):
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

    # Resolve IB/VB/texture inheritance via run = calls
    caller_ctx = {}
    for sec_name, sec_data in sections.items():
        ctx = {
            'ib': sec_data['ib'],
            'vb0': sec_data['vb0'],
            'vb1': sec_data['vb1'],
            'textures': sec_data['textures'].copy(),
        }
        for run_target in sec_data['runs']:
            if run_target in sections:
                caller_ctx[run_target] = ctx
                if sections[run_target]['runs']:
                    for rt2 in sections[run_target]['runs']:
                        if rt2 in sections:
                            caller_ctx[rt2] = ctx

    # Build final draw calls
    draw_calls = []
    for sec_name, sec_data in sections.items():
        if not sec_data['draws']:
            continue

        ctx = caller_ctx.get(sec_name, {})
        eff_ib = sec_data['ib'] if sec_data['ib'] is not None else ctx.get('ib')
        eff_vb0 = sec_data['vb0'] if sec_data['vb0'] is not None else ctx.get('vb0')
        eff_vb1 = sec_data['vb1'] if sec_data['vb1'] is not None else ctx.get('vb1')
        eff_tex = {**ctx.get('textures', {}), **sec_data['textures']}

        for dc in sec_data['draws']:
            dc['ib_resource'] = eff_ib
            dc['vb0_resource'] = eff_vb0
            dc['vb1_resource'] = eff_vb1
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
    # Build index: full relative path -> full path
    all_files_rel = {}  # relative path (with forward slashes) -> absolute path
    all_files_name = {}  # filename only -> absolute path (first found)
    for root, dirs, files in os.walk(ini_dir):
        for f in files:
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, ini_dir).replace('\\', '/')
            all_files_rel[rel.lower()] = fp
            all_files_rel[rel] = fp
            if f not in all_files_name:
                all_files_name[f] = fp
    
    resolved = {}
    for rname, rdata in resources.items():
        if 'filename' not in rdata:
            continue
        
        fname = rdata['filename'].replace('\\', '/')
        fp = None
        
        # Try 1: exact relative path from ini_dir
        candidate = os.path.join(ini_dir, fname)
        if os.path.exists(candidate):
            fp = candidate
        # Try 2: search by full relative path
        elif fname in all_files_rel:
            fp = all_files_rel[fname]
        elif fname.lower() in all_files_rel:
            fp = all_files_rel[fname.lower()]
        # Try 3: search by filename only (last resort)
        elif os.path.basename(fname) in all_files_name:
            fp = all_files_name[os.path.basename(fname)]
        
        if fp and os.path.exists(fp):
            resolved[rname] = {
                'path': fp,
                'stride': rdata.get('stride', 0),
                'format': rdata.get('format', ''),
                'type': rdata.get('type', ''),
            }
    
    return resolved


def load_mesh_data(resolved, draw_calls, rotate=True, mirror_x=False):
    """Load vertex/UV/index data per IB, auto-matching VB resources by name prefix."""
    # Find unique IBs
    ib_set = set()
    for dc in draw_calls:
        ib = dc.get('ib_resource')
        if ib:
            ib_set.add(ib)

    mesh_data = {}

    for ib_name in ib_set:
        if ib_name not in resolved:
            continue

        indices = read_indices(resolved[ib_name]['path'])

        # Extract hash/prefix from IB name to match VB resources
        # Handles both formats:
        #   Hash: Resource_b3c6ea5a_Component1 -> b3c6ea5a
        #   Named: ResourceSunnaBodyAIB -> SunnaBody
        ib_clean = ib_name.replace('Resource', '').replace('_', '')
        import re as _re
        hash_match = _re.search(r'[0-9a-f]{8}', ib_clean.lower())
        if hash_match:
            ib_hash = hash_match.group()
        else:
            # Named format: strip suffixes
            ib_hash = ib_clean
            for suffix in ['AIB', 'BIB', 'IB', 'Component1', 'Component2', 'Component3']:
                ib_hash = ib_hash.replace(suffix, '')
            ib_hash = ib_hash.lower()

        pos_res = None
        uv_res = None

        for rname in resolved:
            rlower = rname.lower()
            if 'position' in rlower:
                if ib_hash and ib_hash in rlower.replace('_', ''):
                    pos_res = rname
            elif 'texcoord' in rlower:
                if ib_hash and ib_hash in rlower.replace('_', ''):
                    uv_res = rname

        positions = []
        if pos_res and pos_res in resolved:
            r = resolved[pos_res]
            positions = read_positions(r['path'], r['stride'], rotate=rotate, mirror_x=mirror_x)

        uvs = []
        if uv_res and uv_res in resolved:
            r = resolved[uv_res]
            uvs = read_uvs(r['path'], r['stride'])

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
    """Load texture into Blender."""
    print(f"  [Migoto] load_dds: {name} -> {filepath}")
    if not filepath:
        print(f"  [Migoto]   ERROR: filepath is None")
        return None
    if not os.path.exists(filepath):
        print(f"  [Migoto]   ERROR: file does not exist")
        return None
    if name in bpy.data.images:
        print(f"  [Migoto]   Already loaded")
        return bpy.data.images[name]
    
    # Try 1: direct load
    try:
        img = bpy.data.images.load(filepath)
        img.name = name
        print(f"  [Migoto]   OK (direct)")
        return img
    except Exception as e:
        print(f"  [Migoto]   Direct failed: {e}")
    
    # Try 2: convert DDS->PNG via Pillow
    if filepath.lower().endswith('.dds'):
        try:
            from PIL import Image as PILImage
            png_path = filepath.rsplit('.', 1)[0] + '.png'
            if not os.path.exists(png_path):
                PILImage.open(filepath).save(png_path)
                print(f"  [Migoto]   Converted to: {png_path}")
            img = bpy.data.images.load(png_path)
            img.name = name
            print(f"  [Migoto]   OK (converted)")
            return img
        except ImportError:
            print(f"  [Migoto]   Pillow not available")
        except Exception as e:
            print(f"  [Migoto]   Convert failed: {e}")
    
    print(f"  [Migoto]   FAILED")
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
    filename_ext = ".ini"
    filter_glob: StringProperty(default="*.ini", options={'HIDDEN'})
    rotate_model: BoolProperty(name="应用 -90° X 旋转 / Apply -90° X Rotation", default=True)
    mirror_x: BoolProperty(name="镜像 X 轴 / Mirror X Axis", description="左右翻转模型 / Flip model left-right", default=False)
    split_parts: BoolProperty(name="分离部件 / Split Into Parts", default=True)
    load_textures: BoolProperty(name="加载贴图 / Load Textures", default=True)

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

        # Load mesh data per IB
        mesh_data = load_mesh_data(resolved, draw_calls, rotate=self.rotate_model, mirror_x=self.mirror_x)

        # Build material map: IB -> diffuse texture path
        ib_textures = {}  # ib_resource_name -> diffuse file path
        for dc in draw_calls:
            ib = dc.get('ib_resource')
            if not ib or ib in ib_textures:
                continue
            tex_ref = dc.get('textures', {}).get('diffuse')
            if tex_ref and tex_ref in resolved:
                ib_textures[ib] = resolved[tex_ref]['path']
                print(f"  [Migoto] {ib} -> {tex_ref} -> {resolved[tex_ref]['path']}")
            else:
                ib_textures[ib] = None

        # Fallback: if no texture refs found, scan directories
        if all(v is None for v in ib_textures.values()):
            print(f"  [Migoto] No INI texture refs found, scanning directories...")
            # Build file index
            tex_files = {}  # relative_path -> absolute_path
            for sdir in search_dirs:
                for root, dirs, files in os.walk(sdir):
                    for f in files:
                        if f.lower().endswith(('.dds', '.png', '.jpg')):
                            fp = os.path.join(root, f)
                            rel = os.path.relpath(fp, sdir).replace('\\', '/').lower()
                            tex_files[rel] = fp
            
            # Match by IB name -> directory pattern
            for ib in ib_textures:
                ib_lower = ib.lower()
                best = None
                for rel, fp in tex_files.items():
                    rel_lower = rel.lower()
                    # Skip non-color textures
                    if any(skip in rel_lower for skip in ['normal', 'lightmap', 'materialmap', 'fx', 'wengine', 'toggle', 'menu', 'slot']):
                        continue
                    # Body IB -> textures/body/ directory
                    if 'body' in ib_lower and 'body' in rel_lower:
                        best = fp
                        break
                    # Hair IB -> textures/hair/ or resources/ with hair
                    elif 'hair' in ib_lower and ('hair' in rel_lower or 'head' in rel_lower):
                        best = fp
                        break
                
                # Fallback: first valid texture
                if not best:
                    for rel, fp in tex_files.items():
                        rel_lower = rel.lower()
                        if any(skip in rel_lower for skip in ['normal', 'lightmap', 'materialmap', 'fx', 'wengine', 'toggle', 'menu', 'slot']):
                            continue
                        best = fp
                        break
                
                ib_textures[ib] = best
                if best:
                    print(f"  [Migoto] {ib} -> auto: {best}")
                else:
                    print(f"  [Migoto] {ib} -> no texture found")

        # Use mod root for collection path
        mod_root = search_dirs[-1]
        
        # Create collection
        mod_name = safe_name(os.path.splitext(os.path.basename(ini_path))[0])
        coll = bpy.data.collections.new(mod_name)
        coll['migoto_ini_dir'] = mod_root
        context.scene.collection.children.link(coll)

        # Create materials per IB
        materials = {}
        for ib_name, tex_path in ib_textures.items():
            mat_name = f"{mod_name}_{ib_name.replace('Resource', '').replace('AIB', '').replace('BIB', '').replace('IB', '')}"
            is_hair = 'hair' in ib_name.lower()
            materials[ib_name] = make_material(mat_name, diffuse=tex_path, alpha=is_hair)

        obj_count = 0

        if self.split_parts:
            for dc in draw_calls:
                ib = dc.get('ib_resource')
                if not ib or ib not in mesh_data:
                    continue

                md = mesh_data[ib]
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
                mat = materials.get(dc.get('ib_resource'))

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
                if not ib or ib not in mesh_data:
                    continue
                md = mesh_data[ib]
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

        # Set up variant visibility
        # Group objects by condition, hide non-first variants
        variant_conditions = set()
        for dc in draw_calls:
            cond = dc.get('condition')
            if cond:
                variant_conditions.add(cond)

        if variant_conditions:
            # Sort conditions for consistent ordering
            sorted_conditions = sorted(variant_conditions)
            first_cond = sorted_conditions[0]

            # Store variant info on collection
            coll['migoto_variant_var'] = '$swapkey0'  # TODO: detect variable name
            coll['migoto_variant_conditions'] = '\n'.join(sorted_conditions)

            # Tag and hide objects
            for obj in coll.objects:
                if obj.type != 'MESH':
                    continue
                # Find which condition this object belongs to
                obj_cond = obj.get('migoto_condition', None)
                if obj_cond:
                    obj.hide_viewport = (obj_cond != first_cond)
                    obj.hide_render = (obj_cond != first_cond)

        self.report({'INFO'}, f"导入 {obj_count} 个对象 / Imported {obj_count} objects")
        return {'FINISHED'}


def menu_fn(self, context):
    self.layout.operator(IMPORT_OT_3dmigoto.bl_idname, text="3Dmigoto 模型 / 3Dmigoto Model (.ini)")


# ============================================================
# Variant Texture Switcher UI
# ============================================================

class MIGOTO_PG_variant(bpy.types.PropertyGroup):
    """Store a texture variant option."""
    name: bpy.props.StringProperty(name="名称 / Name")
    filepath: bpy.props.StringProperty(name="路径 / File Path")


class MIGOTO_PG_material_variants(bpy.types.PropertyGroup):
    """Store all variants for one material slot."""
    material_name: bpy.props.StringProperty(name="材质 / Material")
    current_index: bpy.props.IntProperty(name="当前 / Current", default=0)
    variants: bpy.props.CollectionProperty(type=MIGOTO_PG_variant)


class MIGOTO_PG_model_variants(bpy.types.PropertyGroup):
    """Top-level storage for all material variants in the imported model."""
    model_name: bpy.props.StringProperty(name="模型 / Model")
    materials: bpy.props.CollectionProperty(type=MIGOTO_PG_material_variants)
    active_material: bpy.props.IntProperty(name="激活 / Active", default=0)


class MIGOTO_OT_switch_variant(bpy.types.Operator):
    """Switch texture variant for a material"""
    bl_idname = "migoto.switch_variant"
    bl_label = "切换变体 / Switch Variant"
    bl_options = {'REGISTER', 'UNDO'}

    material_index: bpy.props.IntProperty()
    variant_index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, 'migoto_variants'):
            return {'CANCELLED'}

        model_var = scene.migoto_variants
        if self.material_index >= len(model_var.materials):
            return {'CANCELLED'}

        mat_var = model_var.materials[self.material_index]
        if self.variant_index >= len(mat_var.variants):
            return {'CANCELLED'}

        variant = mat_var.variants[self.variant_index]
        mat_var.current_index = self.variant_index

        # Find the material and update its texture
        mat_name = mat_var.material_name
        if mat_name not in bpy.data.materials:
            return {'CANCELLED'}

        mat = bpy.data.materials[mat_name]
        if not mat.use_nodes:
            return {'CANCELLED'}

        # Find the image texture node
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                # Load new texture
                fp = variant.filepath
                if os.path.exists(fp):
                    new_img = bpy.data.images.load(fp)
                    new_img.name = f"{mat_name}_{variant.name}"
                    node.image = new_img
                    break

        return {'FINISHED'}


class MIGOTO_OT_scan_variants(bpy.types.Operator):
    """Scan imported model directory for texture variants"""
    bl_idname = "migoto.scan_variants"
    bl_label = "扫描贴图变体 / Scan Texture Variants"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        # Find the INI file path from the imported collection
        # Look for the most recently imported migoto collection
        ini_path = None
        for coll in bpy.data.collections:
            # Check if collection has migoto mesh objects
            has_mesh = any(obj.type == 'MESH' for obj in coll.objects)
            if has_mesh:
                # Try to find INI path from a custom property
                if 'migoto_ini_dir' in coll:
                    ini_path = coll['migoto_ini_dir']
                    break

        if not ini_path or not os.path.isdir(ini_path):
            self.report({'ERROR'}, 'No imported model found. Import a model first.')
            return {'CANCELLED'}

        # Create variant storage
        if not hasattr(scene, 'migoto_variants'):
            scene.migoto_variants = bpy.props.PointerProperty(type=MIGOTO_PG_model_variants)

        model_var = scene.migoto_variants
        model_var.materials.clear()

        # Scan for texture variants in the INI directory
        texture_groups = {}  # category -> list of (name, filepath)

        for root, dirs, files in os.walk(ini_path):
            for f in files:
                if not f.lower().endswith(('.dds', '.png', '.jpg')):
                    continue
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, ini_path).replace('\\', '/')

                # Categorize by directory
                parts = rel.split('/')
                if len(parts) >= 2:
                    category = parts[-2]  # parent directory name
                else:
                    category = 'root'

                name = os.path.splitext(f)[0]
                if category not in texture_groups:
                    texture_groups[category] = []
                texture_groups[category].append((name, fp))

        # Create material variant entries for body textures
        for category, variants in texture_groups.items():
            if len(variants) < 2:
                continue  # Skip single-variant categories

            # Find which material uses this category
            mat_name = None
            for mat in bpy.data.materials:
                if not mat.use_nodes:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        img_path = bpy.path.abspath(node.image.filepath)
                        for vname, vpath in variants:
                            if os.path.normpath(img_path) == os.path.normpath(vpath):
                                mat_name = mat.name
                                break
                    if mat_name:
                        break
                if mat_name:
                    break

            if not mat_name:
                # Guess material from category
                cat_lower = category.lower()
                for mat in bpy.data.materials:
                    if cat_lower in mat.name.lower():
                        mat_name = mat.name
                        break

            if not mat_name:
                continue

            # Add to variant storage
            mat_var = model_var.materials.add()
            mat_var.material_name = mat_name
            for vname, vpath in sorted(variants):
                v = mat_var.variants.add()
                v.name = vname
                v.filepath = vpath

            # Set current to match loaded texture
            for i, (_, vpath) in enumerate(sorted(variants)):
                mat = bpy.data.materials.get(mat_name)
                if mat and mat.use_nodes:
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            img_path = bpy.path.abspath(node.image.filepath)
                            if os.path.normpath(img_path) == os.path.normpath(vpath):
                                mat_var.current_index = i
                                break

        self.report({'INFO'}, f'Found {len(model_var.materials)} material variant groups')
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

            # Group by condition
            cond_objects = {}  # condition -> [obj_name, ...]
            for obj in coll.objects:
                if obj.type != 'MESH':
                    continue
                cond = obj.get('migoto_condition', None)
                if cond:
                    if cond not in cond_objects:
                        cond_objects[cond] = []
                    cond_objects[cond].append(obj.name)

            if not cond_objects:
                self.report({'INFO'}, 'No variant conditions found')
                return {'CANCELLED'}

            # Create one group with all variants
            mg.groups.clear()
            g = mg.groups.add()
            g.name = "变体 / Variants"
            for cond, obj_names in sorted(cond_objects.items()):
                for oname in obj_names:
                    item = g.items.add()
                    item.obj_name = oname
            # Hide all but first
            for i, item in enumerate(g.items):
                obj = bpy.data.objects.get(item.obj_name)
                if obj:
                    obj.hide_viewport = (i != 0)
                    obj.hide_render = (i != 0)
            g.active_index = 0

            # Also group always-visible objects separately
            always_visible = []
            for obj in coll.objects:
                if obj.type == 'MESH' and not obj.get('migoto_condition'):
                    always_visible.append(obj.name)

            break

        self.report({'INFO'}, f'Created {len(mg.groups)} groups')
        return {'FINISHED'}


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
    """3Dmigoto Model Variant Panel"""
    bl_label = "3Dmigoto 变体 / Variants"
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

        # Scan variants button
        layout.operator('migoto.scan_variants', text='扫描变体 / Scan Variants', icon='VIEWZOOM')

        # Export textures button
        layout.operator('migoto.export_textures', text='导出贴图 / Export Textures', icon='EXPORT')
        layout.separator()

        # Texture variants
        if not hasattr(scene, 'migoto_variants'):
            layout.label(text='未加载变体 / No variants loaded')
            return

        model_var = scene.migoto_variants
        if not model_var.materials:
            layout.label(text='未找到变体 / No variants found')
            return

        for mat_idx, mat_var in enumerate(model_var.materials):
            box = layout.box()
            box.label(text=mat_var.material_name, icon='MATERIAL')

            for var_idx, variant in enumerate(mat_var.variants):
                row = box.row()
                is_active = (mat_var.current_index == var_idx)
                icon = 'RADIOBUT_ON' if is_active else 'RADIOBUT_OFF'
                op = row.operator('migoto.switch_variant', text=variant.name, icon=icon)
                op.material_index = mat_idx
                op.variant_index = var_idx


classes = (
    MIGOTO_PG_variant,
    MIGOTO_PG_material_variants,
    MIGOTO_PG_model_variants,
    MIGOTO_PG_mesh_group_item,
    MIGOTO_PG_mesh_group,
    MIGOTO_PG_mesh_groups,
    MIGOTO_OT_switch_variant,
    MIGOTO_OT_add_group,
    MIGOTO_OT_remove_group,
    MIGOTO_OT_add_obj_to_group,
    MIGOTO_OT_remove_obj_from_group,
    MIGOTO_OT_toggle_obj_visibility,
    MIGOTO_OT_show_all_in_group,
    MIGOTO_OT_hide_all_in_group,
    MIGOTO_OT_rename_group,
    MIGOTO_OT_auto_group,
    MIGOTO_OT_scan_variants,
    MIGOTO_OT_export_textures,
    MIGOTO_PT_mesh_groups,
    MIGOTO_PT_panel,
    IMPORT_OT_3dmigoto,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_fn)
    bpy.types.Scene.migoto_variants = bpy.props.PointerProperty(type=MIGOTO_PG_model_variants)
    bpy.types.Scene.migoto_mesh_groups = bpy.props.PointerProperty(type=MIGOTO_PG_mesh_groups)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_fn)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.migoto_variants
    del bpy.types.Scene.migoto_mesh_groups

if __name__ == "__main__":
    register()
