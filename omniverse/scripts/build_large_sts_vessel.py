"""Build one full-scale container vessel spanning STS01--STS03.

The reference drawings define the arrangement, not exact naval dimensions:
30 odd 20-foot bays, bridge between 43/45, 40-foot stern bay 62,
13 rows and separate below/above-deck tier numbering. Existing other berths
and yard inventory are untouched. Repeated cargo uses native USD instances.
"""
import json
import random
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
from large_vessel_layout import (
    ROOT, LAYER, SHIP, REPLACED, ROWS, BAYS, HOLD_TIERS, DECK_TIERS,
    DECK_Z, SHIP_X, BEAM_M, bay_y, row_x, tier_z,
)
from large_vessel_details import smooth_stations, deck_height, build_fittings, line_tubes


def build():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    for path in REPLACED:
        stage.OverridePrim(path).SetActive(False)
    root = UsdGeom.Xform.Define(stage, SHIP)
    root.AddTranslateOp().Set(Gf.Vec3d(SHIP_X, 0, -4.9478))
    stage.GetRootLayer().documentation = "Single STS01-03 vessel. Layout follows supplied bay/row/tier diagrams; dimensions are project design values, not TOS measurements."

    def attr(prim, name, kind, value):
        prim.CreateAttribute(name, kind, custom=True).Set(value)

    for name, val in {"lengthMeters": 248., "beamMeters": BEAM_M,
                      "bridgeY": 162.8, "deckZ": DECK_Z}.items():
        attr(root.GetPrim(), "ship:" + name, Sdf.ValueTypeNames.Double, val)
    attr(root.GetPrim(), "ship:layoutVersion", Sdf.ValueTypeNames.String, "sts01-03-reference-v1")
    attr(root.GetPrim(), "ship:servingCranes", Sdf.ValueTypeNames.StringArray, ["STS_01", "STS_02", "STS_03"])
    attr(root.GetPrim(), "ship:source", Sdf.ValueTypeNames.String, "User reference drawings; illustrative cargo, not a live manifest")
    looks = SHIP + "/Looks"

    def material(name, color, metal=0., rough=.7):
        m = UsdShade.Material.Define(stage, looks + "/" + name)
        sh = UsdShade.Shader.Define(stage, str(m.GetPath()) + "/Surface")
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
        sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
        sh.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        return m

    hull = material("HullNavy", (.025, .045, .065), .35)
    antifoul = material("AntifoulingRed", (.26, .055, .028))
    deck = material("WorkingDeck", (.21, .27, .25), .2)
    steel = material("HatchSteel", (.36, .42, .4), .35)
    white = material("WarmWhite", (.78, .79, .72))
    glass = material("BridgeGlass", (.018, .068, .095), .55, .22)
    dark = material("DarkSteel", (.035, .04, .045), .6)
    orange = material("LifeboatOrange", (.9, .2, .035))
    bronze = material("MarineBronze", (.32, .23, .09), .65, .5)
    colors = [(.075,.22,.37),(.43,.1,.055),(.11,.34,.32),(.58,.49,.28),(.43,.47,.46),(.07,.12,.19)]
    cargo_mats = [material("Cargo" + str(i), c, .2) for i, c in enumerate(colors)]

    def bind(prim, mat):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)

    def cube(path, pos, size, mat):
        c = UsdGeom.Cube.Define(stage, path)
        c.CreateSizeAttr(1)
        c.AddTranslateOp().Set(Gf.Vec3d(*pos))
        c.AddScaleOp().Set(Gf.Vec3f(*size))
        bind(c.GetPrim(), mat)
        return c.GetPrim()

    def mesh(path, points, faces, mat, smooth=False):
        m = UsdGeom.Mesh.Define(stage, path)
        m.CreatePointsAttr(points)
        m.CreateFaceVertexCountsAttr([len(f) for f in faces])
        m.CreateFaceVertexIndicesAttr([i for f in faces for i in f])
        m.CreateSubdivisionSchemeAttr("none")
        m.CreateDoubleSidedAttr(True)
        if smooth:
            normals=[Gf.Vec3d(0) for _ in points]
            for face in faces:
                a,b,c=(Gf.Vec3d(*points[i]) for i in face[:3])
                n=Gf.Cross(b-a,c-a)
                for i in face:normals[i]+=n
            m.CreateNormalsAttr([Gf.Vec3f(n.GetNormalized()) for n in normals])
            m.SetNormalsInterpolation('vertex')
        bind(m.GetPrim(), mat)
        return m

    # A genuinely continuous hull, with a pointed bow and rounded transom.
    station_keys = [(-4,.12),(-1,2),(3,6),(8,10.5),(14,14.7),(20,16.8),
                (28,17.6),(48,17.6),(100,17.6),(155,17.6),(205,17.6),
                (227,17.3),(237,15.8),(242,13.8),(244,11.8)]
    stations = smooth_stations([(y,w*BEAM_M/35.2) for y,w in station_keys])
    def shell(name, z0, z1, lower_factor, upper_factor, mat):
        points=[]
        underwater=name=='UnderwaterBody'
        profile=([(-.52,0),(.52,0),(.78,.08),(.94,.28),(.98,.65),(.98,1),
                  (-.98,1),(-.98,.65),(-.94,.28),(-.78,.08)] if underwater else
                 [(-lower_factor,0),(lower_factor,0),(.995,.65),(upper_factor,1),
                  (-upper_factor,1),(-.995,.65)])
        n=len(profile)
        for y,w in stations:
            flare=max(0.,min(1.,(20-y)/24))**2
            for xf,zf in profile:
                z=z0+(z1-z0)*zf
                if not underwater:z=z0+(deck_height(y,DECK_Z)-z0)*zf
                # Raked stem with a recessed keel and full upper forecastle.
                yy=y+flare*((12-8*zf) if underwater else 4*(1-zf))
                points.append((w*xf,yy,z))
        faces=[]
        for i in range(len(stations)-1):
            a=i*n;b=a+n
            for j in range(n):
                # Leave the deck open: hatch covers below close cargo holds.
                if j != (5 if underwater else 3):faces.append((a+j,b+j,b+(j+1)%n,a+(j+1)%n))
        faces.extend([tuple(reversed(range(n))),tuple(range(len(points)-n,len(points)))])
        mesh(SHIP+"/Hull/"+name,points,faces,mat,smooth=True)
    shell("UnderwaterBody",-12.8,-.15,.62,.98,antifoul)
    shell("ContinuousTopsides",-.15,DECK_Z,.98,1.,hull)
    # Bow/stern platforms and continuous side walkways.
    fore = [(y,w) for y,w in stations if y<=28]
    def deck_panels(path,sections):
        # A single non-planar outline lets Hydra choose long diagonal faces
        # across the raised bow, lifting the cargo deck through box bottoms.
        # These narrow quads are each planar and follow the section height.
        points=[(x,y,deck_height(y,DECK_Z)+.15) for y,w in sections for x in (-w,0.,w)]
        faces=[]
        for i in range(len(sections)-1):
            for side in (0,1):
                a=i*3+side
                faces.append((a,a+1,a+4,a+3))
        mesh(path,points,faces,deck)
    deck_panels(SHIP+'/Deck/Forecastle',fore)
    aft=[(y,w) for y,w in stations if y>=222]
    deck_panels(SHIP+'/Deck/PoopDeck',aft)
    for side in (-1,1):
        cube(SHIP+f"/Deck/SideWalk{side:+d}".replace('+','P').replace('-','M'),(side*(BEAM_M/2-.55),130,DECK_Z+.05),(1.,204,.28),deck)
        # One polyline per railing avoids hundreds of separate rail meshes.
        pts=[]; counts=[]
        for z in (DECK_Z+.55,DECK_Z+1.05):
            pts.extend([(side*(BEAM_M/2-.25),28,z),(side*(BEAM_M/2-.25),232,z)]);counts.append(2)
        for y in range(28,233,4):
            pts.extend([(side*(BEAM_M/2-.25),y,DECK_Z),(side*(BEAM_M/2-.25),y,DECK_Z+1.1)]);counts.append(2)
        lines=[];offset=0
        for count in counts:
            lines.append(pts[offset:offset+count]);offset+=count
        line_tubes(mesh,SHIP+f"/Deck/Rail{'Port' if side<0 else 'Starboard'}",lines,.045,white)

    # Pairs of 20-foot longitudinal stations share a 40-foot hatch cover.
    for first in range(1,60,4):
        second=first+2
        y=(bay_y(first)+bay_y(second))/2
        base=SHIP+f"/Hatches/Hatch_{first:02d}_{second:02d}"
        h=UsdGeom.Xform.Define(stage,base).GetPrim()
        attr(h,"ship:bays",Sdf.ValueTypeNames.IntArray,[first,second])
        xs=(-11.,0.,11.) if first==1 else (-13.,0.,13.)
        for section,x in enumerate(xs):
            cube(base+f"/Cover{section}",(x,y,DECK_Z),(10.8 if first==1 else 12.8,12.5,.25),steel)
        for end in (-1,1):
            cube(base+f"/Coaming{'F' if end<0 else 'A'}",(0,y+end*6.2,DECK_Z-.15),(33.3 if first==1 else 39.2,.12,.5),dark)

    # Accommodation block between odd bays 43 and 45, as in the reference.
    bridge=SHIP+"/Bridge"
    # Close the accommodation gap in the otherwise open cargo deck.
    cube(bridge+"/AccommodationDeck",(0,162.8,DECK_Z-.25),(BEAM_M-1.4,19.7,.5),steel)
    cube(bridge+"/DeckhouseFoundation",(0,162.8,DECK_Z-.15),(28.,13.,.6),white)
    for level,(z,w,l) in enumerate([(DECK_Z+1.4,28.,13.),(DECK_Z+4.1,25.,12.),
                                   (DECK_Z+6.8,23.,11.),(DECK_Z+9.5,21.,10.)]):
        cube(bridge+f"/Deckhouse{level}",(0,162.8,z),(w,l,2.7),white)
        cube(bridge+f"/Overhang{level}",(0,162.8,z+1.4),(w+.8,l+.7,.18),steel)
        for side in (-1,1):
            for k in range(9):
                cube(bridge+f"/Window{level}_{side}_{k}".replace('-','M'),
                     ((k-4)*2.1,162.8+side*(l/2+.025),z+.3),(1.3,.08,.8),glass)
    cube(bridge+"/Wheelhouse",(0,162.8,DECK_Z+12.3),(29.,10.8,2.5),white)
    for side in (-1,1):
        cube(bridge+f"/Panorama{'F' if side<0 else 'A'}",(0,162.8+side*5.43,DECK_Z+12.6),(28.,.1,1.25),glass)
        cube(bridge+f"/Wing{'L' if side<0 else 'R'}",(side*15.4,162.8,DECK_Z+11.3),(2.3,10.8,.25),white)
        cube(bridge+f"/Lifeboat{'L' if side<0 else 'R'}",(side*15.,162.8,DECK_Z+3.7),(2.6,7.,2.),orange)
    cube(bridge+"/BridgeRoof",(0,162.8,DECK_Z+13.6),(30.,11.7,.25),white)
    cube(bridge+"/Mast",(0,163.8,DECK_Z+16.5),(.32,.32,6.),white)
    cube(bridge+"/Radar",(0,163.8,DECK_Z+18.),(4.,.25,.25),dark)
    cube(bridge+"/Funnel",(-8,166.5,DECK_Z+14.4),(3.2,3.,5.),dark)
    cube(bridge+"/FunnelStripe",(-8,166.5,DECK_Z+15.2),(3.3,3.1,.6),orange)

    # Reusable detailed, corrugated standard-sized container prototypes.
    from ship_yard_container_models import create_yard_prototypes,PAINTS
    native=create_yard_prototypes(stage)
    prototypes={(feet,index):native[feet,paint] for feet in (20,40) for index,paint in enumerate(PAINTS)}

    rng=random.Random(20261102)
    count=0
    def container(path,x,y,tier,feet,bay,rowsize=None):
        nonlocal count
        c=UsdGeom.Xform.Define(stage,path)
        c.GetPrim().GetReferences().AddInternalReference(prototypes[feet,rng.randrange(len(colors))])
        c.GetPrim().SetInstanceable(True)
        c.AddTranslateOp().Set(Gf.Vec3d(x,y,tier_z(tier)))
        for n,v in (("sizeFt",feet),("bay",bay),("tier",tier)):
            attr(c.GetPrim(),"ship:"+n,Sdf.ValueTypeNames.Int,v)
        if rowsize is not None:
            attr(c.GetPrim(),"ship:row",Sdf.ValueTypeNames.Int,rowsize)
            attr(c.GetPrim(),"ship:location",Sdf.ValueTypeNames.String,f"{bay:02d}{rowsize:02d}{tier:02d}")
        count+=1

    # Use standard 40-foot pairs mixed with two independent 20-foot boxes.
    # Taper the first bays and the last bay to follow the hull outline.
    for first in range(1,60,4):
        second=first+2
        even=first+1
        y=(bay_y(first)+bay_y(second))/2
        for row in ROWS:
            if first==1 and abs(row_x(row))>10.3:continue
            for tier in DECK_TIERS:
                if first in (1,57) and tier>86:continue
                special1=(first==13 and row==12 and tier==90)
                special3=(first==53 and row==12 and tier==90)
                if special3:continue
                base=SHIP+f"/Model/Bay{even:02d}/R{row:02d}_T{tier:02d}"
                if special1:
                    container(SHIP+"/Model/ShipContainer179",row_x(row),y,tier,40,even,row)
                elif (first//4+row)%5==0:
                    for bay in (first,second):container(base+f"/C{bay:02d}",row_x(row),bay_y(bay),tier,20,bay,row)
                else:container(base,row_x(row),y,tier,40,even,row)
    # Two accessible 20-foot boxes for the existing STS03 round-trip demo.
    for name,bay in (("UnitA",53),("UnitB",55)):
        container(SHIP+"/Model/STS03_Double20/"+name,row_x(12),bay_y(bay),90,20,bay,12)
    for row in (6,4,2,0,1,3,5):
        for tier in DECK_TIERS:
            container(SHIP+f"/Model/Bay62/R{row:02d}_T{tier}",row_x(row),bay_y(62),tier,40,62,row)

    for first in range(1,60,4):
        y=(bay_y(first)+bay_y(first+2))/2
        for tier in HOLD_TIERS:
            half_width=10.7+(tier-2)/14*5.4
            if first==1:half_width=min(half_width,10.3)
            for row in ROWS:
                if abs(row_x(row))+1.219>half_width:continue
                container(SHIP+f"/Model/HoldBay{first+1:02d}/R{row:02d}_T{tier:02d}",
                          row_x(row),y,tier,40,first+1,row)

    # Batched seven-segment deck numerals; no font or texture dependency.
    segments={0:'abcdef',1:'bc',2:'abged',3:'abgcd',4:'fgbc',5:'afgcd',
              6:'afgecd',7:'abc',8:'abcdefg',9:'abfgcd'}
    rects={'a':(.12,.88,.88,1.),'g':(.12,.44,.88,.56),'d':(.12,0,.88,.12),
           'f':(0,.52,.12,.92),'b':(.88,.52,1.,.92),
           'e':(0,.08,.12,.48),'c':(.88,.08,1.,.48)}
    points=[];faces=[]
    for bay in BAYS:
        for index,digit in enumerate(f'{bay:02d}'):
            for segment in segments[int(digit)]:
                u0,v0,u1,v1=rects[segment]
                start=len(points)
                for u,v in ((u0,v0),(u1,v0),(u1,v1),(u0,v1)):
                    label_x = 14. if bay == 1 else BEAM_M/2-.75
                    if bay == 62:label_x=12.
                    points.append((label_x+(v-.5)*.85,bay_y(bay)+(index-.5)*.55+(u-.5)*.43,DECK_Z+.21))
                faces.append(tuple(range(start,start+4)))
    mesh(SHIP+'/Deck/BayNumbers',points,faces,white)

    # Logical slots remain distinct from the illustrative deck-cargo manifest.
    for bay in BAYS:
        p=UsdGeom.Xform.Define(stage,SHIP+f"/BayPlan/Bay{bay:02d}")
        p.AddTranslateOp().Set(Gf.Vec3d(0,bay_y(bay),0))
        for n,kind,val in [("bay",Sdf.ValueTypeNames.Int,bay),("sizeFt",Sdf.ValueTypeNames.Int,40 if bay==62 else 20),
                           ("rows",Sdf.ValueTypeNames.IntArray,[6,4,2,0,1,3,5] if bay==62 else list(ROWS)),("holdTiers",Sdf.ValueTypeNames.IntArray,[] if bay==62 else list(HOLD_TIERS)),
                           ("deckTiers",Sdf.ValueTypeNames.IntArray,list(DECK_TIERS))]:attr(p.GetPrim(),"ship:"+n,kind,val)
    attr(root.GetPrim(),"ship:containerCount",Sdf.ValueTypeNames.Int,count)

    build_fittings(stage,SHIP,mesh,cube,(hull,steel,white,dark,orange,glass,bronze),DECK_Z,stations)
    stage.GetRootLayer().Export(str(LAYER))
    return {"layer":str(LAYER),"containers":count,"length_m":248,"bays":list(BAYS)}


if __name__ == "__main__":
    print(json.dumps(build()))
