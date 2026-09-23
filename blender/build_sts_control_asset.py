"""Build the STS kinematic visual asset from the existing USD prototype.

Run with Blender --background --python blender/build_sts_control_asset.py.
Coordinates and limits use the existing model's scene metres, not PLC units.
"""
from pathlib import Path
import json
import math
import bpy
import bmesh
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'omniverse/assets/rtg/STS_Crane_Controlled.usdc'
PIVOT = Vector((0, 0.94, 9.36))
SOURCE_LEG_TRACK_GAUGE_M = 4.2
TARGET_LEG_TRACK_GAUGE_M = 4.8
FIXED_TRACK_CENTER_Y_M = 4.02
BOOM_WATERSIDE_SCALE = 1.30
CRANE_HEIGHT_SCALE = 1.20
XBRACE_LOWER_SCALE = 0.6875
XBRACE_TOP_Z = 8.316941


def empty(name, parent=None, location=(0, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.location = location
    return obj


def attach(obj, parent):
    world = obj.matrix_world.copy()
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.matrix_world = world


def cut(obj, front):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.bisect_plane(
        bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
        dist=0.000001, plane_co=PIVOT, plane_no=(0, 1, 0),
        clear_outer=front, clear_inner=not front,
    )
    boundary = [e for e in bm.edges if e.is_boundary and
                all(abs(v.co.y - PIVOT.y) < 0.0001 for v in e.verts)]
    if boundary:
        bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.usd_import(
        filepath=str(ROOT / 'omniverse/assets/rtg/STS_Crane_Static_Visual.usdc'),
        import_visible_only=True, import_materials=True, import_usd_preview=True,
        support_scene_instancing=False,
    )
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    # Bake the prototype's centering transform before rebuilding parent groups.
    for obj in meshes:
        matrix = obj.matrix_world.copy()
        obj.data = obj.data.copy()
        obj.data.transform(matrix)
        obj.parent = None
        obj.matrix_world = Matrix.Identity(4)
    for obj in list(bpy.data.objects):
        if obj.type != 'MESH':
            bpy.data.objects.remove(obj, do_unlink=True)
    gantry = empty('Gantry')
    fixed = empty('Fixed', gantry)
    boom = empty('Boom', gantry, PIVOT)
    trolley = empty('Trolley', gantry)
    hoist = empty('Hoist', trolley)
    # Widen the two fixed leg lines slightly so the controlled wheels land on
    # the widened quay rails. Keep the centerline fixed while scaling only the
    # fixed structure; boom/trolley/hoist geometry remains unchanged.
    fixed_scale_y = TARGET_LEG_TRACK_GAUGE_M / SOURCE_LEG_TRACK_GAUGE_M
    fixed.scale.y = fixed_scale_y
    fixed.location.y = FIXED_TRACK_CENTER_Y_M * (1.0 - fixed_scale_y)
    bpy.context.view_layer.update()
    counts = dict(fixed=0, boom=0, trolley=0, hoist=0, split=0, omitted=0)
    for obj in meshes:
        name = obj.name.lower()
        # The original prototype includes 104 m of fixed track and sleepers.
        if any(k in name for k in ('_sleeper_', '_rail_front', '_rail_back',
                                    'hoist_rope', 'stay_cable')) or \
                'orange_brace_x' in name:
            # The source prototype has four cross-members between the legs
            # (two on each side). They are not part of the target quay-crane
            # structure and should stay out of every controlled instance.
            bpy.data.objects.remove(obj, do_unlink=True)
            counts['omitted'] += 1
            continue
        if 'spreader' in name:
            attach(obj, hoist)
            counts['hoist'] += 1
        elif 'trolley' in name and 'trolley_rail' not in name:
            attach(obj, trolley)
            counts['trolley'] += 1
        else:
            lo = Vector(tuple(min(v.co[i] for v in obj.data.vertices) for i in range(3)))
            hi = Vector(tuple(max(v.co[i] for v in obj.data.vertices) for i in range(3)))
            candidate = lo.z >= 8.85 and hi.z <= 10.7 and lo.y < PIVOT.y - 0.001
            hinge_cover = 'pivot' in name and lo.y < PIVOT.y < hi.y
            if candidate and not hinge_cover:
                if hi.y > PIVOT.y + 0.001:
                    rear = obj.copy()
                    rear.data = obj.data.copy()
                    rear.name = obj.name + '_Rear'
                    bpy.context.scene.collection.objects.link(rear)
                    cut(rear, False)
                    attach(rear, fixed)
                    cut(obj, True)
                    counts['split'] += 1
                attach(obj, boom)
                counts['boom'] += 1
            else:
                attach(obj, fixed)
                counts['fixed'] += 1
    bpy.context.view_layer.update()
    # Extend the ship-side boom section about the hinge after all meshes have
    # been attached, so parent scaling is not cancelled by re-parenting.
    # The controlled trolley range is widened in sts_control.py to use it.
    boom.scale.y = BOOM_WATERSIDE_SCALE
    # Raise the complete crane structure together so the columns become 20%
    # taller without disconnecting the boom, trolley, or hoist from the legs.
    gantry.scale.z = CRANE_HEIGHT_SCALE
    # Shorten the side X-braces from their old wheel-level endpoints so their
    # lower ends land on the lower portal crossbeam instead.
    for obj in meshes:
        if 'orange_xbrace' not in obj.name.lower():
            continue
        for vertex in obj.data.vertices:
            vertex.co.z = XBRACE_TOP_Z - (XBRACE_TOP_Z - vertex.co.z) * XBRACE_LOWER_SCALE
        obj.data.update()
    bpy.context.view_layer.update()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # The runtime creates variable-length hoist ropes and boom stay cylinders.
    bpy.ops.wm.usd_export(
        filepath=str(OUT), root_prim_path='/STSCrane', export_materials=True,
        generate_preview_surface=True, export_animation=False,
        export_lights=False, export_cameras=False, selected_objects_only=False,
    )
    print('STS_ASSET_BUILT', json.dumps(counts), str(OUT))


if __name__ == '__main__':
    main()
