import json
import sys
from pathlib import Path

def inspect(path):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    reader=STEPControl_Reader()
    if reader.ReadFile(str(path))!=IFSelect_RetDone: raise ValueError('不是可讀 STEP')
    if reader.TransferRoots()<1: raise ValueError('STEP 沒有可轉換的根實體')
    shape=reader.OneShape()
    if shape.IsNull(): raise ValueError('STEP 為空')
    bbox=Bnd_Box(); BRepBndLib.Add_s(shape,bbox)
    x0,y0,z0,x1,y1,z1=bbox.Get()
    props=GProp_GProps(); BRepGProp.VolumeProperties_s(shape,props)
    solids=0; explorer=TopExp_Explorer(shape,TopAbs_SOLID)
    while explorer.More(): solids+=1; explorer.Next()
    return {'source_path':str(path),'valid':bool(BRepCheck_Analyzer(shape).IsValid()),'solids':solids,'volume':float(props.Mass()),'bbox_extents':[x1-x0,y1-y0,z1-z0],'units':'OCCT import working units; confirm source units before acceptance'}

if __name__=='__main__':
    try: print(json.dumps(inspect(Path(sys.argv[1])),ensure_ascii=True))
    except Exception as exc:
        print(str(exc),file=sys.stderr); sys.exit(1)
