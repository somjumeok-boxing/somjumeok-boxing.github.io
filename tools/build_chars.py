"""솜주먹 캐릭터를 블렌더(bpy)로 만들어 GLB로 내보낸다.

- 부위별 강체 파트(머리/몸통/반바지/팔/다리 등)를 메타볼로 매끈하게 만들고,
  게임의 관절 그룹 원점에 맞춘 좌표로 둔다. (이음매는 둥근 관절이 겹쳐 가림)
- 좌표는 게임(three.js) 기준 (x 앞, y 위, z 옆)으로 적고, 블렌더 좌표로 바꿔 넣는다.
- 오브젝트 이름: '<캐릭터>_<파트>_<재질>'  예) kong_head_skin
  (three.js GLTFLoader가 이름의 점(.)을 지우므로 밑줄을 쓴다)
실행: python build_chars.py <출력.glb>
"""
import sys
import math
import bpy
import bmesh
from mathutils import Vector, Quaternion

OUT = sys.argv[-1]
MB_K = 1.742
HEM = 0.0  # 셔츠 밑단 높이(torso 원점=엉덩이 기준)  # 메타볼 반지름 → 실제 표면 반지름 보정 (단일 공 기준 측정값)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def B(x, y, z):
    """three 좌표 → 블렌더 좌표"""
    return Vector((x, -z, y))


def srgb(h):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (lin((h >> 16) & 255), lin((h >> 8) & 255), lin(h & 255), 1.0)


_mats = {}


def material(name, hexcol, rough=0.75, vcol=False):
    key = (name, hexcol, rough, vcol)
    if key in _mats:
        return _mats[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = srgb(hexcol) if not vcol else (1, 1, 1, 1)
    bsdf.inputs['Roughness'].default_value = rough
    if vcol:
        attr = m.node_tree.nodes.new('ShaderNodeVertexColor')
        attr.layer_name = 'Col'
        m.node_tree.links.new(attr.outputs['Color'], bsdf.inputs['Base Color'])
    m.use_backface_culling = False
    _mats[key] = m
    return m


def finish(me, name, mat, decimate=None, smooth=0):
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name.replace('.', '_'), me)
    scene.collection.objects.link(ob)
    ob.data.materials.append(mat)
    if decimate or smooth:
        if smooth:
            # 메타볼 이음매의 잘록한 굴곡을 펴서 매끈하게
            sm = ob.modifiers.new('sm', 'SMOOTH')
            sm.factor = 0.8; sm.iterations = smooth
        if decimate:
            mod = ob.modifiers.new('dec', 'DECIMATE')
            mod.ratio = decimate
        dg = bpy.context.evaluated_depsgraph_get()
        me2 = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
        ob.modifiers.clear()
        ob.data = me2
        for p in me2.polygons:
            p.use_smooth = True
    return ob


def blob(name, mat, elems, res=0.01, decimate=0.12, smooth=0):
    """elems: (kind, (x,y,z), R, scale(x,y,z) or None, threshold-free)
    kind: 'ball' | 'ell'"""
    mb = bpy.data.metaballs.new(name + '_mb')
    mb.resolution = res
    mb.render_resolution = res
    mb.threshold = 0.6
    # 메타볼은 이름의 점(.) 앞부분이 같으면 한 덩어리로 섞이므로 점 없는 고유 이름을 쓴다
    o = bpy.data.objects.new('mb_' + name.replace('.', '_'), mb)
    scene.collection.objects.link(o)
    for kind, co, R, sc in elems:
        e = mb.elements.new()
        e.co = B(*co)
        e.radius = R * MB_K
        if kind == 'ell':
            e.type = 'ELLIPSOID'
            sx, sy, sz = sc
            # three (x,y,z) 크기 → 블렌더 (x, z, y)
            e.size_x, e.size_y, e.size_z = sx, sz, sy
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    bpy.data.objects.remove(o)
    return finish(me, name, mat, decimate, smooth)


def tube(y0, y1, r0, r1, step=0.025, x=0.0, z=0.0):
    """y0→y1 사이에 공을 촘촘히 늘어놓아 굵기가 고르게 변하는 매끈한 원기둥(팔다리용)"""
    n = max(2, int(abs(y1 - y0) / step) + 1)
    return [('ball', (x, y0 + (y1 - y0) * i / (n - 1), z), r0 + (r1 - r0) * i / (n - 1), None) for i in range(n)]


def sphere(name, mat, co, R, sc=(1, 1, 1), seg=20):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=max(8, seg // 2), radius=R)
    s = B(*sc)
    for v in bm.verts:
        v.co = Vector((v.co.x * sc[0], v.co.y * sc[2], v.co.z * sc[1])) + B(*co)
    bm.to_mesh(me)
    bm.free()
    return finish(me, name, mat)


def ring(name, mat, y, r, h, seg=32):
    """y축 둘레 띠(반바지 허리, 글러브 손목 등)"""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=seg, radius1=r, radius2=r * 0.985, depth=h)
    for v in bm.verts:
        v.co = v.co + B(0, y, 0)
    bm.to_mesh(me)
    bm.free()
    return finish(me, name, mat)


def shell_from(src, name, mat, offset, keep):
    """src 메시를 법선 방향으로 부풀려 keep(x,y,z)가 참인 부분만 남긴 껍데기(머리카락 캡)"""
    bm = bmesh.new()
    bm.from_mesh(src.data)
    bm.normal_update()
    for v in bm.verts:
        v.co = v.co + v.normal * offset
    dead = []
    for v in bm.verts:
        x, y, z = v.co.x, v.co.z, -v.co.y  # 블렌더 → three
        if not keep(x, y, z):
            dead.append(v)
    bmesh.ops.delete(bm, geom=dead, context='VERTS')
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return finish(me, name, mat)


def cut_below(ob, y_cut):
    """three 좌표 y가 y_cut 아래인 부분을 잘라내 셔츠 밑단을 가로로 깔끔하게 만든다"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    dead = [v for v in bm.verts if v.co.z < y_cut]
    bmesh.ops.delete(bm, geom=dead, context='VERTS')
    bm.to_mesh(ob.data)
    bm.free()


def stripe_uv(ob, period=0.045):
    """원통 투영 UV: u=둘레 각도, v=높이/줄 간격 → 게임에서 가로줄 텍스처를 반복"""
    me = ob.data
    uv = me.uv_layers.new(name='UVMap')
    for loop in me.loops:
        co = me.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (math.atan2(co.y, co.x) / (2 * math.pi) + 0.5, co.z / period)


def stripe_colors(ob, base, stripe, period=0.045, width=0.4):
    me = ob.data
    attr = me.color_attributes.new('Col', 'FLOAT_COLOR', 'POINT')
    cb, cs = srgb(base), srgb(stripe)
    for i, v in enumerate(me.vertices):
        y = v.co.z
        f = (y / period) % 1.0
        attr.data[i].color = cs if f < width else cb


# ---------------------------------------------------------------- 공통 몸
def body(pfx, look):
    skin = material(pfx + '_skin', 0xffd6b8, 0.68)
    shirt = material(pfx + '_shirt', look['shirt'], 0.85)
    shorts = material(pfx + '_shorts', look['shorts'], 0.9)
    trim = material(pfx + '_trim', look['trim'], 0.85)
    glove = material(pfx + '_glove', look['glove'], 0.45)
    cuff = material(pfx + '_cuff', 0xf4f4f2, 0.7)

    # 체형 계수: w = 몸통·반바지 폭, k = 팔다리 굵기 (밤톨은 더 마른 체형)
    w = look.get('w', 1.0)
    k = look.get('k', 1.0)
    belly = look.get('belly', 1.0)  # 배 볼록함

    # 몸통 (torso 그룹 원점 = 엉덩이)
    # 셔츠: 배~가슴~반바지 속까지 한 덩어리 (밑단 덧대기 없이 매끈하게, 아래로 갈수록 좁아져 반바지 안에 숨음)
    # 셔츠: 배·가슴·등을 한 덩어리로 만든 뒤 밑단(y=HEM)에서 가로로 잘라 반바지 위에 덮이게
    t = blob(pfx + '.torso.shirt', shirt, [
        ('ell', (0.03 * belly, 0.15, 0), 0.212, (1.06 * w * (0.85 + 0.15 * belly), 1.12, 1.1 * w)),  # 둥근 배
        ('ell', (0.0, 0.31, 0), 0.19, (0.95 * w, 0.85, 1.15 * w)),     # 가슴
        ('ell', (-0.03, 0.34, 0), 0.16, (0.9 * w, 0.9, 1.1 * w)),      # 등
        ('ell', (0.0, 0.03, 0), 0.2, (1.02 * w, 0.5, 1.07 * w)),       # 밑단 쪽 폭(반바지 허리보다 살짝 넓게)
    ], res=0.008, decimate=None)
    cut_below(t, HEM)
    dec = t.modifiers.new('dec', 'DECIMATE'); dec.ratio = 0.12
    dg = bpy.context.evaluated_depsgraph_get()
    me2 = bpy.data.meshes.new_from_object(t.evaluated_get(dg)); t.modifiers.clear(); t.data = me2
    for p_ in me2.polygons:
        p_.use_smooth = True
    if look.get('stripes'):
        stripe_uv(t)  # 줄무늬는 게임에서 텍스처로 입힘
    blob(pfx + '.torso.skin', skin, [
        ('ball', (0, 0.44, 0), 0.07 * k, None),
        ('ball', (0, 0.5, 0), 0.068 * k, None),
    ], res=0.008)
    # 반바지 (hips 그룹)
    blob(pfx + '.hips.shorts', shorts, [
        ('ell', (-0.02, -0.03, 0), 0.19, (1.05 * w, 0.72, 1.1 * w)),
        ('ball', (0.0, -0.12, 0.105 * w), 0.098 * k, None),
        ('ball', (0.0, -0.12, -0.105 * w), 0.098 * k, None),
        ('ell', (0.0, 0.07, 0), 0.19, (1.0 * w, 0.42, 1.04 * w)),    # 허리: 셔츠 밑단 안쪽으로 들어감(겹쳐서 숙여도 틈 없음)
    ], res=0.009, decimate=0.1)

    for i, side in ((0, 1), (1, -1)):
        # 위팔 (어깨 그룹) - 민소매라 어깨는 맨살
        blob(f'{pfx}.arm{i}.upper.skin', skin,
             [('ball', (0, 0, 0), 0.078 * k, None)] + tube(-0.04, -0.22, 0.07 * k, 0.063 * k),
             res=0.008, smooth=6)
        # 아래팔 + 글러브 (팔꿈치 그룹)
        blob(f'{pfx}.arm{i}.fore.skin', skin, tube(0.0, -0.14, 0.062 * k, 0.055 * k), res=0.008, smooth=6)
        blob(f'{pfx}.arm{i}.fore.glove', glove, [
            ('ell', (0.0, -0.245, 0), 0.098, (1.05, 1.2, 1.0)),
            ('ell', (0.035, -0.205, -side * 0.06), 0.045, (1.0, 1.3, 0.9)),  # 엄지(몸 안쪽)
            ('ball', (0.0, -0.17, 0), 0.08, None),
        ], res=0.007)
        ring(f'{pfx}.arm{i}.fore.cuff', cuff, -0.155, 0.08, 0.05)
        # 다리
        blob(f'{pfx}.leg{i}.thigh.skin', skin, tube(-0.02, -0.25, 0.096 * k, 0.079 * k), res=0.008, smooth=6)
        blob(f'{pfx}.leg{i}.shin.skin', skin, tube(0.0, -0.18, 0.08 * k, 0.064 * k) + [
            ('ell', (0.045, -0.232, 0), 0.07, (1.6, 0.58, 1.08 * k)),           # 발
            ('ball', (0.13, -0.235, side * 0.025), 0.03, None),            # 발가락
        ], res=0.008, smooth=4)
    return skin


# ---------------------------------------------------------------- 머리
def potato_head(pfx, look, skin):
    hair = material(pfx + '_hair', look['hair'], 0.95)
    ink = material(pfx + '_ink', 0x141216, 0.25)
    blush = material(pfx + '_blush', 0xff9c94, 0.8)
    lip = material(pfx + '_lip', 0xd9776a, 0.6)
    head = blob(pfx + '.head.skin', skin, [
        ('ell', (-0.01, 0.035, 0), 0.158, (1.0, 1.02, 0.97)),   # 좁은 정수리
        ('ell', (0.01, -0.05, 0), 0.165, (1.0, 0.8, 1.13)),     # 넓은 볼·턱(감자 아랫부분)
        ('ell', (0.085, -0.06, 0.103), 0.088, (0.95, 0.9, 0.95)),  # 빵빵한 볼
        ('ell', (0.085, -0.06, -0.103), 0.088, (0.95, 0.9, 0.95)),
        ('ell', (-0.045, 0.115, 0.06), 0.06, (1, 0.55, 1)),     # 감자 혹
        ('ell', (0.06, -0.12, 0), 0.07, (1, 0.6, 1.3)),         # 이중턱
        ('ell', (-0.01, -0.15, 0), 0.075, (1, 1.2, 1)),         # 목
        ('ell', (0.17, -0.02, 0), 0.024, (0.8, 0.75, 1.15)),    # 코
        ('ell', (-0.005, -0.005, 0.168), 0.04, (0.75, 1.05, 0.45)),  # 귀
        ('ell', (-0.005, -0.005, -0.168), 0.04, (0.75, 1.05, 0.45)),
    ], res=0.0065, decimate=0.12)
    # 바짝 깎은 머리: 얼굴 껍데기를 살짝 부풀려 이마선 위만 남김
    shell_from(head, pfx + '.head.hair', hair, 0.007,
               lambda x, y, z: (y > 0.045 + 0.09 * max(0.0, x / 0.17) and abs(z) < 0.2 or (x < -0.02 and y > -0.07 + 0.04 * abs(z) / 0.17))
               and not (abs(z) > 0.15 and y < 0.04))
    for z in (0.058, -0.058):
        sphere(pfx + '.head.ink', ink, (0.152, 0.02, z), 0.018, (0.55, 0.42, 1.35), seg=16)   # 가늘게 뜬 눈
        sphere(pfx + '.head.ink', ink, (0.152, 0.063, z * 1.05), 0.026, (0.3, 0.22, 1.0), seg=12)  # 눈썹
        sphere(pfx + '.head.blush', blush, (0.158, -0.055, z * 1.75), 0.03, (0.3, 0.55, 1.0), seg=14)
    sphere(pfx + '.head.lip', lip, (0.168, -0.083, 0), 0.016, (0.6, 0.65, 1.3), seg=14)       # 오므린 입


def girl_head(pfx, look, skin):
    hair = material(pfx + '_hair', look['hair'], 0.7)
    ink = material(pfx + '_ink', 0x141216, 0.2)
    white = material(pfx + '_white', 0xffffff, 0.3)
    blush = material(pfx + '_blush', 0xff9cab, 0.8)
    head = blob(pfx + '.head.skin', skin, [
        ('ell', (0, 0.005, 0), 0.168, (0.97, 1.04, 0.93)),
        ('ell', (0.07, -0.06, 0.08), 0.07, (1, 0.9, 1)),
        ('ell', (0.07, -0.06, -0.08), 0.07, (1, 0.9, 1)),
        ('ell', (0.06, -0.1, 0), 0.07, (1, 0.8, 1.2)),
        ('ell', (-0.01, -0.15, 0), 0.068, (1, 1.2, 1)),         # 목
        ('ell', (0.163, -0.015, 0), 0.018, (0.8, 0.8, 1.1)),
        ('ell', (0, -0.005, 0.158), 0.035, (0.7, 1, 0.45)),
        ('ell', (0, -0.005, -0.158), 0.035, (0.7, 1, 0.45)),
    ], res=0.0065, decimate=0.12)
    # 머리카락 캡 + 뒷머리 숱 + 앞머리
    shell_from(head, pfx + '.head.hair', hair, 0.018,
               lambda x, y, z: (y > 0.03 + 0.1 * max(0.0, x / 0.17)) or (x < 0.02))
    blob(pfx + '.head.hairvol', hair, [
        ('ell', (-0.07, 0.03, 0), 0.17, (0.8, 1.0, 1.05)),
        ('ell', (-0.06, -0.1, 0.07), 0.11, (0.8, 1.3, 0.9)),
        ('ell', (-0.06, -0.1, -0.07), 0.11, (0.8, 1.3, 0.9)),
        ('ell', (0.0, -0.08, 0.15), 0.06, (1.0, 1.9, 0.6)),     # 옆머리
        ('ell', (0.0, -0.08, -0.15), 0.06, (1.0, 1.9, 0.6)),
    ], res=0.007)
    blob(pfx + '.head.bangs', hair, [
        ('ell', (0.135, 0.085, z), 0.05, (0.55, 1.15, 0.8)) for z in (-0.09, -0.045, 0.0, 0.045, 0.09)
    ] + [('ell', (0.09, 0.13, 0), 0.1, (0.8, 0.5, 1.5))], res=0.006)
    for z in (0.064, -0.064):
        sphere(pfx + '.head.ink', ink, (0.152, 0.0, z), 0.024, (0.55, 1.15, 1.0), seg=18)    # 큰 눈
        sphere(pfx + '.head.white', white, (0.163, 0.013, z - 0.007), 0.0075, (1, 1, 1), seg=10)
        sphere(pfx + '.head.blush', blush, (0.15, -0.055, z * 1.5), 0.028, (0.3, 0.55, 1.0), seg=14)
    # 웃는 입: 작은 공을 호 모양으로 이어 붙임
    blob(pfx + '.head.mouth', ink, [
        ('ball', (0.163 - 0.008 * (1 - (k / 3) ** 2), -0.072 - 0.012 * (1 - (k / 3) ** 2), 0.007 * k), 0.006, None)
        for k in range(-3, 4)
    ], res=0.002, decimate=None)


KONG = dict(shirt=0xf3d313, shorts=0x4b4e58, trim=0xf3d313, glove=0x232327, hair=0x18181a)
BAM = dict(w=0.68, k=0.76, belly=0.2, shirt=0xf6eef2, stripes=0xd99ab4, shorts=0xf2bfcf, trim=0xf2bfcf, glove=0xf7f7f5, hair=0x17120f)

sk = body('kong', KONG)
potato_head('kong', KONG, sk)
sb = body('bam', BAM)
girl_head('bam', BAM, sb)

tris = sum(len(o.data.polygons) for o in scene.objects if o.type == 'MESH')
print('objects', len(scene.objects), 'polys', tris)
bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLB', export_yup=True, export_apply=True,
                          export_vertex_color='ACTIVE', export_normals=True, export_materials='EXPORT')
print('saved', OUT)
