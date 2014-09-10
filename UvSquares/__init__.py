#    <Uv Squares, Blender addon for reshaping UV vertices to grid.>
#    Copyright (C) <2014> <Reslav Hollos>
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name": "Uv Squares",
    "description": "Reshapes UV faces to a grid "
    "of either equivalent squares or by respect to shape, " 
    "aligns vertices on axis and can make them equally distanced, " 
    "rips/joins faces.",
    "author": "Reslav Hollos",
    "version": (1, 3, 14),
    "blender": (2, 71, 0),
    "category": "Mesh",
    #"location": "UV Image Editor > UVs > UVs to grid of squares",
    #"warning": "",
    "wiki_url": "http://wiki.blender.org/index.php/Extensions:2.6/Py/Scripts/UV/Uv_Squares"
    }

import bpy
import bmesh
from collections import defaultdict
from math import radians, hypot
import time

#todo: set scale when image ratio is not 1:1
#todo: deselect points that are part of edgeFaces but not part of selFaces

#known_issue: if loop cut was used, mesh has to be unwrapped again
#known_issue: if there are 4 corners but it says there aren't: undo/join/or unwrap again
#known_issue: if cursor doesn't snap to nearest corner: scale everything up a bit

def main1(context, callsNo = 0, respectShape = True, equalLine = True, horizontal = None):
    allowedRecursion = 5
    callsNo += 1
    if callsNo >= allowedRecursion:
        return ErrorFinished("exceeded recursion limit of " + callsNo)
   
    startTime = time.clock()
    allowedTime = 17
    precision = 3
    
    obj = context.active_object
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    uv_layer = bm.loops.layers.uv.verify()
    bm.faces.layers.tex.verify()  # currently blender needs both layers.
    
    global allowedFaces; allowedFaces = 2200
    if len(bm.faces) > allowedFaces:
        return ErrorFinished("selected more than " +str(allowedFaces) +"allowed faces.") 
 
    selVerts, filteredVerts, selFaces, edgeFaces, vertsDict= ListsOfVerts(uv_layer, bm, startTime, allowedTime, precision)  #remember selected verts so we can reselect at end
    
    if (len(filteredVerts) <= 1):
        return 
    
    lucv, ldcv, rucv, rdcv = Corners(selVerts, filteredVerts[:], selFaces, vertsDict)      #left up corner vert, ...
    
    cursorClosestTo = CursorClosestTo(filteredVerts)
    if len(selFaces) is 0:
        VertsDictForLine(uv_layer, bm, precision, selVerts, vertsDict)
        
        if AreVectsLinedOnAxis(filteredVerts) is False:
            ScaleTo0OnAxisAndCursor(filteredVerts, vertsDict, cursorClosestTo, horizontal)
            return SuccessFinished(me, startTime)
        
        elif equalLine is True:
            MakeEqualDistanceBetweenVertsInLine(filteredVerts, vertsDict, precision, cursorClosestTo)
            return SuccessFinished(me, startTime)
        
        else: return ErrorFinished("corner number mismatch, exactly 4 needed")
              
    else:
        corners = [lucv, ldcv, rucv, rdcv]
        cursorClosestTo = CursorClosestTo(corners)
        if len(filteredVerts) is 4:
            lucf, ldcf, rucf, rdcf = MakeCornerUvFacesFrom4Corners(lucv, ldcv, rucv, rdcv)
        else:   
            lucf, ldcf, rucf, rdcf = CornerFaces(uv_layer, lucv, ldcv, rucv, rdcv, selFaces)
            if lucf is None or ldcf is None or rucf is None or rdcf is None: 
                return ErrorFinished("not allowed corner number.")
            SetCornerFaces(uv_layer, lucv, ldcv, rucv, rdcv, lucf, ldcf, rucf, rdcf)

        if lucf is None or ldcf is None or rucf is None or rdcf is None: 
            return ErrorFinished("not all corner face parts were recognized.")
        
        facesArray2d = Build2DimArrayOfUvFaces(uv_layer, selFaces, lucf, ldcf, rucf, rdcf, startTime, allowedTime)
        if facesArray2d is "retry":
            ErrorFinished("2d array of faces was not built. Rotating and retrying")
            angle = 11.23
            if lucv.x == min(lucv.x, rdcv.x):
                angle = -angle
            return RotateAndRecall(context, callsNo, uv_layer, selVerts, edgeFaces, vertsDict, angle, cursorClosestTo)
        
        elif facesArray2d is None:
            return
        
        #respect shape
        cursorOrient = "leftUp"
        if AreVectorsQuasiEqual(cursorClosestTo, rucv): cursorOrient = "rightUp"
        elif AreVectorsQuasiEqual(cursorClosestTo, ldcv): cursorOrient = "leftDown"
        elif AreVectorsQuasiEqual(cursorClosestTo, rdcv): cursorOrient = "rightDown"

        if respectShape is True:
            if RespectShape(context, uv_layer, bm, startTime, allowedTime, precision, facesArray2d, vertsDict, edgeFaces, cursorOrient, horizontal) is not "skipped":
                
                #reselect 
                DeselectAll()
                for key in vertsDict:
                    for v in vertsDict[(key[0], key[1])]:
                        v.select = True
          
                #restore cursor
                SetAll2dCursorsTo(cursorClosestTo.x, cursorClosestTo.y)
                
                return SuccessFinished(me, startTime)
      
        print("skipping since shape is in grid")
        MakeUvFacesEqualRectangles(uv_layer, precision, vertsDict, edgeFaces, facesArray2d, lucv, ldcv, rucv, rdcv, cursorClosestTo)
        
        #restore cursor
        SetAll2dCursorsTo(cursorClosestTo.x, cursorClosestTo.y)
        return SuccessFinished(me, startTime)

def RespectShape(context, uv_layer, bm, startTime, allowedTime, precision, array2dOfVerts, vertsDict_, edgeFaces, cursorOrient, horizontal):
    vertsDict = vertsDict_.copy()
    #we add verts of first closest faces, otherwise the selection would rip
    for f in edgeFaces:
        for l in f.face.loops:
            luv = l[uv_layer]
            x = round(luv.uv.x, precision)
            y = round(luv.uv.y, precision)
            
            vertsDict[(x,y)].append(luv)
    
    #check if shape is already in a grid, so we MakeUvFacesEqualRectangles
    allowedError = 0.0000001
   
    #check first row 's UP verts
    rowChecksum1 = array2dOfVerts[0][0].leftUpVert.y
    for face in array2dOfVerts[0]:
        if (abs(face.rightUpVert.y - rowChecksum1) > allowedError):
            rowChecksum1 = None
            break
    
    #check all row's right verts
    for row in array2dOfVerts:
        rowChecksum2 = row[0].leftDownVert.y
        for face in row:
            if (abs(face.rightDownVert.y - rowChecksum2) > allowedError):
                rowChecksum2 = None
                break
        if rowChecksum2 is None: break
    
    #check first column's left verts
    colChecksum1 = array2dOfVerts[0][0].leftUpVert.x
    for row in array2dOfVerts:
        if (abs(row[0].leftDownVert.x - colChecksum1) > allowedError):
            colChecksum1 = None
            break
            
    #check all column's right verts
    j = 0
    while (j < len(array2dOfVerts[0])):
        colChecksum2 = array2dOfVerts[0][j].rightUpVert.x
        for row in array2dOfVerts:
            if (abs(row[j].rightDownVert.x - colChecksum2) > allowedError):
                colChecksum2 = None
                break
        if colChecksum2 is None: break
        j += 1
    
    difference = rowChecksum1 is None or rowChecksum2 is None or colChecksum1 is None or colChecksum2 is None
    if difference is False:
        return "skipped"
        
    #1. select first row's up verts and align to axis
    DeselectAll()
    for face in array2dOfVerts[0]:
        x = round(face.leftUpVert.x, precision)
        y = round(face.leftUpVert.y, precision)    
        for v in vertsDict[(x,y)]:
            v.select = True

        x = round(face.rightUpVert.x, precision)
        y = round(face.rightUpVert.y, precision)    
        for v in vertsDict[(x,y)]:
            v.select = True
    
    #if leftUp or leftDown
    x = array2dOfVerts[0][0].leftUpVert.x
    y = array2dOfVerts[0][0].leftUpVert.y
    #if rightUp or rightDown
    if cursorOrient is "rightUp" or cursorOrient is "rightDown":
        x = array2dOfVerts[0][len(array2dOfVerts[0])-1].rightUpVert.x
        y = array2dOfVerts[0][len(array2dOfVerts[0])-1].rightUpVert.y
    
    SetAll2dCursorsTo(x, y)
    main1(context, 0, True, False, True)
   
    #2. select from first to last row's down verts and align to axis
    for row in array2dOfVerts:
        DeselectAll()
        for face in row:

            x = round(face.leftDownVert.x, precision)
            y = round(face.leftDownVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.select = True

            x = round(face.rightDownVert.x, precision)
            y = round(face.rightDownVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.select = True                
     
        #if leftUp or leftDown
        x = row[0].leftDownVert.x
        y = row[0].leftDownVert.y
        #if rightUp or rightDown
        if cursorOrient is "rightUp" or cursorOrient is "rightDown":
            x = row[len(row)-1].rightDownVert.x
            y = row[len(row)-1].rightDownVert.y
        
        SetAll2dCursorsTo(x, y)
        main1(context, 0, True, False, True)
    
    #finished horizontal, updating vertsDict
    DeselectAll()
    for key in vertsDict:
        for v in vertsDict[(key[0], key[1])]:
            v.select = True
            
    selVerts, filteredVerts, selFaces, edgeFaces, vertsDict= ListsOfVerts(uv_layer, bm, startTime, allowedTime, precision)  #remember selected verts so we can reselect at end
    
    #3. select first column's left verts and align to axis
    
    DeselectAll()
   
    for row in array2dOfVerts:
        
        for face in row:
            x = round(face.leftUpVert.x, precision)
            y = round(face.leftUpVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.select = True

            x = round(face.leftDownVert.x, precision)
            y = round(face.leftDownVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.select = True
            break
    
    #if leftUp or rightUp
    x = array2dOfVerts[0][0].leftUpVert.x
    y = array2dOfVerts[0][0].leftUpVert.y
    #if leftDown or rightDown
    if (cursorOrient == "leftDown" or cursorOrient == "rightDown"):
        x = array2dOfVerts[len(array2dOfVerts)-1][0].leftDownVert.x
        y = array2dOfVerts[len(array2dOfVerts)-1][0].leftDownVert.y
   
    SetAll2dCursorsTo(x, y)
    main1(context, 0, True, False, False)
    DeselectAll()
    #4. select from first to last column's right verts and align to axis
    i, j = 0, 0 
    #for number of columns
    while(j < len(array2dOfVerts[0])):
        #go by each row
        i = 0
        while(i < len(array2dOfVerts)):
            
            face = array2dOfVerts[i][j]
            
            x = round(face.rightUpVert.x, precision)
            y = round(face.rightUpVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.select = True

            x = round(face.rightDownVert.x, precision)
            y = round(face.rightDownVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.select = True
            
            i += 1
       
        #if leftUp or rightUp
        x = array2dOfVerts[0][j].rightUpVert.x
        y = array2dOfVerts[0][j].rightUpVert.y
        #if leftDown or rightDown
        if (cursorOrient == "leftDown" or cursorOrient == "rightDown"):
            x = array2dOfVerts[len(array2dOfVerts)-1][j].rightDownVert.x
            y = array2dOfVerts[len(array2dOfVerts)-1][j].rightDownVert.y
   
        SetAll2dCursorsTo(x, y)
        main1(context, 0, True, False, False)
        DeselectAll()
        j += 1
        
    return

#sym UvSquares
def main2(context, respectShape = True):
    
    obj = context.active_object
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    uv_layer = bm.loops.layers.uv.verify()
    bm.faces.layers.tex.verify()
    
    selVerts, filteredVerts, selFaces, edgeFaces, vertsDict= ListsOfVerts(uv_layer, bm, time.clock(), 17, 4) 
    lucv, ldcv, rucv, rdcv = Corners(selVerts, filteredVerts[:], selFaces, vertsDict)    
    
    setTo = CursorClosestTo([lucv, ldcv, rdcv, rucv]) 
    SetAll2dCursorsTo(setTo.x, setTo.y)
                
    SymmetrySelected("X", "CURSOR")
    main1(context, 0, respectShape, False)
    
    SetAll2dCursorsTo(setTo.x, setTo.y)
    SymmetrySelected("X", "CURSOR")
    return
    
#face rip    
def main3(context):
    startTime = time.clock()
    
    obj = context.active_object
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    
    uv_layer = bm.loops.layers.uv.verify()
    bm.faces.layers.tex.verify()  # currently blender needs both layers.
    
    RipUvFaces(uv_layer, bm)
    return SuccessFinished(me, startTime)

#face join
def main4(context):
    startTime = time.clock()
    precision = 3
    
    obj = context.active_object
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    
    uv_layer = bm.loops.layers.uv.verify()
    bm.faces.layers.tex.verify()  # currently blender needs both layers.
      
    selVerts, filteredVerts, selFaces, edgeFaces, vertsDict= ListsOfVerts(uv_layer, bm, startTime, 17, precision)
    
    JoinUvFaces(uv_layer, bm, precision, selVerts, filteredVerts, vertsDict)
    return SuccessFinished(me, startTime)

#snap to axis
def main5(context):
    main1(context)
    return

#snap to axis and make equal distance
def main6(context):
    main1(context)
    main1(context)
    return

def main7(context):
    main1(context, true)
    return

def ErrorFinished(message = ""):
    print("--error:", message)
    return    

def SuccessFinished(me, startTime):
    bmesh.update_edit_mesh(me)
    #use for backtrack of steps 
    #bpy.ops.ed.undo_push()
    elapsed = round(time.clock()-startTime, 2)
    if (elapsed >= 0.05):
        print("Success! UvSquares has finished, elapsed time:", elapsed, "s.")
    return

def SymmetrySelected(axis, pivot = "MEDIAN"):
    last_pivot = bpy.context.space_data.pivot_point
    bpy.context.space_data.pivot_point = pivot
    bpy.ops.transform.mirror(constraint_axis=(True, False, False), constraint_orientation='GLOBAL', proportional='DISABLED', proportional_edit_falloff='SMOOTH', proportional_size=1)
    bpy.context.space_data.pivot_point = last_pivot
    return

def RotateAndRecall(context, callsNo, uv_layer, precision, selVerts, edgeFaces, vertsDict, angle, cursorV):
    print("callsNo", callsNo)
    
    pivot = None
    if cursorV is not None:
        pivot = 'CURSOR'
        SetAll2dCursorsTo(cursorV.x, cursorV.y)
        
    for f in edgeFaces:
        for l in f.face.loops:
            luv = l[uv_layer]
            
            if luv.uv in selVerts:            
                x = round(luv.uv.x, precision)
                y = round(luv.uv.y, precision)
            
                vertsDict[(x,y)].append(luv)
    
    for d in vertsDict:
        for v in vertsDict[d]:
            v.select = True
    print("rotating and recalling")
    
    RotateSelected(angle, pivot)

    main1(context, callsNo)
    return

def AreVectsLinedOnAxis(verts):
    areLinedX = True
    areLinedY = True
    allowedError = 0.0001
    valX = verts[0].x
    valY = verts[0].y
    for v in verts:
        if abs(valX - v.x) > allowedError:
            areLinedX = False
        if abs(valY - v.y) > allowedError:
            areLinedY = False
    return areLinedX or areLinedY  

def MakeCornerUvFacesFrom4Corners(lucv,ldcv, rucv, rdcv):
    face = UvFace()
    face.leftUpVert = lucv
    face.leftDownVert = ldcv
    face.rightUpVert = rucv
    face.rightDownVert = rdcv
    a = face
    b = face
    c = face
    d = face
    return a,b,c,d

def MakeEqualDistanceBetweenVertsInLine(filteredVerts, vertsDict, precision, startv = None):    
    verts = filteredVerts
    verts.sort(key=lambda x: x[0])      #sort by .x
    
    first = verts[0]
    last = verts[len(verts)-1]
    
    horizontal = True
    if ((last.x - first.x) >0.0001):
        slope = (last.y - first.y)/(last.x - first.x)
        if (slope > 1) or (slope <-1):
            horizontal = False 
    else: 
        horizontal = False
    
    if horizontal is True:
        length = hypot(first.x - last.x, first.y - last.y)
        
        if startv is last:
            currentX = last.x - length
            currentY = last.y
        else:
            currentX = first.x
            currentY = first.y
    else:
        verts.sort(key=lambda x: x[1])  #sort by .y
        verts.reverse()     #reverse because y values drop from up to down
        first = verts[0]
        last = verts[len(verts)-1]
        
        length = hypot(first.x - last.x, first.y - last.y)  # we have to call length here because if it is not Hor first and second can not actually be first and second
        
        if startv is last:
            currentX = last.x
            currentY = last.y + length
        
        else:
            currentX = first.x
            currentY = first.y
        
    numberOfVerts = len(verts)
    finalScale = length / (numberOfVerts-1)
    
    if horizontal is True:
        first = verts[0]
        last = verts[len(verts)-1]
        
        for v in verts:
            x = round(v.x, precision)
            y = round(v.y, precision)
            
            for vert in vertsDict[(x,y)]:
                vert.uv.x = currentX
                vert.uv.y = currentY
            
            currentX = currentX + finalScale
    else:    
        for v in verts:
            x = round(v.x, precision)
            y = round(v.y, precision)
            
            for vert in vertsDict[(x,y)]:
                vert.uv.x = currentX
                vert.uv.y = currentY
            
            currentY = currentY - finalScale
    return

def VertsDictForLine(uv_layer, bm, precision, selVerts, vertsDict):
    for f in bm.faces:
        for l in f.loops:
                luv = l[uv_layer]
                if luv.select is True:
                    x = round(luv.uv.x, precision)
                    y = round(luv.uv.y, precision)
         
                    vertsDict[(x, y)].append(luv)
    return

def ScaleTo0OnAxisAndCursor(filteredVerts, vertsDict, startv = None, horizontal = None):      
    
    verts = filteredVerts
    verts.sort(key=lambda x: x[0])      #sort by .x
    
    first = verts[0]
    last = verts[len(verts)-1]
    
    if horizontal is None:
        horizontal = True
        if ((last.x - first.x) >0.0001):
            slope = (last.y - first.y)/(last.x - first.x)
            if (slope > 1) or (slope <-1):
                horizontal = False 
        else: 
            horizontal = False
    
    if horizontal is True:
        if startv is None:
            startv = first  
        
        SetAll2dCursorsTo(startv.x, startv.y)
        #scale to 0 on Y
        ScaleTo0('Y')
        return
       
    else:
        verts.sort(key=lambda x: x[1])  #sort by .y
        verts.reverse()     #reverse because y values drop from up to down
        first = verts[0]
        last = verts[len(verts)-1]
        if startv is None:
            startv = first  

        SetAll2dCursorsTo(startv.x, startv.y)
        #scale to 0 on X
        ScaleTo0('X')
        return
    
def ScaleTo0(axis):
    last_area = bpy.context.area.type
    bpy.context.area.type = 'IMAGE_EDITOR'
    last_pivot = bpy.context.space_data.pivot_point
    bpy.context.space_data.pivot_point = 'CURSOR'
    
    for area in bpy.context.screen.areas:
        if area.type == 'IMAGE_EDITOR':
            if axis is 'Y':
                bpy.ops.transform.resize(value=(1, 0, 1), constraint_axis=(False, True, False), constraint_orientation='GLOBAL', mirror=False, proportional='DISABLED', proportional_edit_falloff='SMOOTH', proportional_size=1)
            else:
                bpy.ops.transform.resize(value=(0, 1, 1), constraint_axis=(True, False, False), constraint_orientation='GLOBAL', mirror=False, proportional='DISABLED', proportional_edit_falloff='SMOOTH', proportional_size=1)
                

    bpy.context.space_data.pivot_point = last_pivot
    return

def MakeUvFacesEqualRectangles(uv_layer, precision, vertsDict, edgeFaces, array2dOfVerts, lucv, ldcv, rucv, rdcv, startv):
    rowNumber = len(array2dOfVerts) +1 #number of faces +1 equals number of rows, same for column
    colNumber = len(array2dOfVerts[0]) +1
    
    if startv is None:
        startv = lucv
   
    if startv is lucv: 
        initDistance = rucv.x - lucv.x
        finalScale = initDistance / (colNumber - 1)     
        currRowX = lucv.x
        currRowY = lucv.y
    
    elif startv is rucv:
        initDistance = rucv.y - rdcv.y
        finalScale = initDistance / (rowNumber -1)    
        currRowX = rucv.x - finalScale*(colNumber-1)   
        currRowY = rucv.y
       
    elif startv is rdcv:
        initDistance = rdcv.x - ldcv.x
        finalScale = initDistance / (colNumber - 1)     
        currRowX = rdcv.x - finalScale*(colNumber -1)
        currRowY = rdcv.y + finalScale*(rowNumber -1)
        
    else:
        initDistance = lucv.y - ldcv.y
        finalScale = initDistance /(rowNumber-1) 
        currRowX = ldcv.x
        currRowY = ldcv.y +finalScale*(rowNumber-1)

    
    #we add verts of first closest faces, otherwise the selection would rip
    for f in edgeFaces:
        for l in f.face.loops:
            luv = l[uv_layer]
            x = round(luv.uv.x, precision)
            y = round(luv.uv.y, precision)
            
            vertsDict[(x,y)].append(luv)
    
    #here we select only first rows upper left and right
    for face in array2dOfVerts[0]:
        x = round(face.leftUpVert.x, precision)
        y = round(face.leftUpVert.y, precision)
        for v in vertsDict[(x,y)]:
            v.uv.x = currRowX
            v.uv.y = currRowY
            v.select = True
        
        x = round(face.rightUpVert.x, precision)
        y = round(face.rightUpVert.y, precision)    
        for v in vertsDict[(x,y)]:
            v.uv.x = currRowX + finalScale
            v.uv.y = currRowY
            v.select = True
        
        currRowX = currRowX + finalScale
    currRowX = lucv.x
       
    #and now we can select only the bottom left and right ones        
    for row in array2dOfVerts:
        for face in row:

            x = round(face.leftDownVert.x, precision)
            y = round(face.leftDownVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.uv.x = currRowX
                v.uv.y = currRowY - finalScale
                v.select = True

            x = round(face.rightDownVert.x, precision)
            y = round(face.rightDownVert.y, precision)    
            for v in vertsDict[(x,y)]:
                v.uv.x = currRowX + finalScale
                v.uv.y = currRowY - finalScale
                v.select = True
                        
            currRowX = currRowX + finalScale
                     
        currRowX = lucv.x
        currRowY = currRowY - finalScale

    return

def Corners(selVerts, filteredVerts, selFaces, vertsDict):
    #corners.append(filter by vectors that share location)
    corners = filteredVerts
    
    #if there are only 4 "click selected" vertices ('corners' is here holder for filtered selVerts) 
    if len(corners) is 4:
        selVerts[:] = []
        selVerts.extend(corners)
        
    else:
        corners = []
            
        for v in vertsDict:
            if len(vertsDict[v]) is 1:
                corners.append(vertsDict[v][0].uv)
    
    if len(corners) is not 4:
        return None, None, None, None
    
    firstHighest = corners[0]
    for c in corners:
        if c.y > firstHighest.y:
            firstHighest = c    
    corners.remove(firstHighest)
    
    secondHighest = corners[0]
    for c in corners:
        if (c.y > secondHighest.y):
            secondHighest = c
    
    if firstHighest.x < secondHighest.x:
        leftUp = firstHighest
        rightUp = secondHighest
    else:
        leftUp = secondHighest
        rightUp = firstHighest
    corners.remove(secondHighest)
    
    firstLowest = corners[0]
    secondLowest = corners[1]
    
    if firstLowest.x < secondLowest.x:
        leftDown = firstLowest
        rightDown = secondLowest
    else:
        leftDown = secondLowest
        rightDown = firstLowest
    
    #print(leftUp, leftDown, rightUp, rightDown)
    return leftUp, leftDown, rightUp, rightDown

def AreUvFacesEqual(face1, face2):
    if face1.face is face2.face:
        return True
    return False

def Build2DimArrayOfUvFaces(uv_layer, selFaces, lucf, ldcf, rucf, rdcf, startTime, allowedTime):
    array2dOfVerts = []
    
    start = lucf
    end = rucf

    while True:
        if (time.clock() - startTime > allowedTime):
            print("time limit of", allowedTime,"exceeded while building array.")
            return None        
           
        column = UvFacesFromTo(uv_layer, selFaces, start, end)
        if column is None:
            print("--error: column was not built.")
            return None
        
        array2dOfVerts.append(column)
                   
        if AreUvFacesEqual(start, ldcf):
            break
            
        start = FaceDownOf(uv_layer, selFaces, start)
        end = FaceDownOf(uv_layer, selFaces, end)
        
        if start is None or end is None:
            #print("")
            return "retry"
       
    return array2dOfVerts

def UvFacesFromTo(uv_layer, selFaces, start, end):
    column = []
    current = start
    
    #print(startUvF.leftUpVert, startUvF.leftDownVert, startUvF.rightUpVert, startUvF.rightDownVert)
    #print(endUvF.leftUpVert, endUvF.leftDownVert, endUvF.rightUpVert, endUvF.rightDownVert)
    
    if(AreUvFacesEqual(current, end)):
        column.append(current)
        return column
    
    while True:       
        column.append(current)
        
        current = FaceRightOf(uv_layer, selFaces, current)
        
        if current is None:
            print("--error: column returned None, in UvFacesFromTo")
            return None
           
        if AreUvFacesEqual(current, end):
            column.append(current)
            break
        
    return column

def NextUvFace(uv_layer, orientation, selFaces, given):
    if orientation is "right":
        return FaceRightOf(uv_layer, selFaces, given)
    
    elif orientation is "down":
        return FaceDownOf(uv_layer, selFaces, given)
    
    return 

def FaceRightOf(uv_layer, selFaces, given):
    DeselectAll()
    
    contains = [given.rightUpVert, given.rightDownVert]
    notContains = [given.leftUpVert, given.leftDownVert]
    
    face = FaceContaining(uv_layer, selFaces, contains, notContains)
    #since we go from leftup to rightdown we can remove found faces to reduce time take for nextface search 
    try:
        selFaces.remove(face)
    except ValueError:
        pass
    
    SetFaceBy2Corners(uv_layer, "toRight", face, given.rightUpVert, given.rightDownVert)
    
    DeselectAll()
    return face 

def FaceDownOf(uv_layer, selFaces, given):
    DeselectAll()
    
    contains = [given.leftDownVert, given.rightDownVert]
    notContains = [given.leftUpVert, given.rightUpVert]

    try:
        selFaces.remove(given)
    except ValueError:
        pass
    
    face = FaceContaining(uv_layer, selFaces, contains, notContains)
    
    SetFaceBy2Corners(uv_layer, "toDown", face, given.leftDownVert, given.rightDownVert)
    
    DeselectAll()
    return face 

#we remove faces once they are found in FaceRightOf and FaceDownOf and add them in UvFacesFromTo
def FaceContaining(uv_layer, selFaces, contains, notContains = None):
    for f in selFaces:
        selectThisFace = True
        
        #checking for contained verts 
        for cv in contains:
            containsV = False
            for l in f.face.loops:
                luv = l[uv_layer]
                if AreVectorsQuasiEqual(cv, luv.uv):
                    containsV = True
                    break
            
            if containsV is False:
                selectThisFace = False
                break
        
        if selectThisFace is False:
            continue
        
        if notContains is not None:
            #checking for not contained verts
            for ncv in notContains:
                for l in f.face.loops:
                    luv = l[uv_layer]
                    if AreVectorsQuasiEqual(ncv, luv.uv):
                        selectThisFace = False
                        break
        
        if selectThisFace is True:
            return f
   
    return

def CornerFaces(uv_layer, lucv, ldcv, rucv, rdcv, selFaces):   
    lucf, ldcf, rucf, rdcf = None, None, None, None
    for face in selFaces: 
        for l in face.face.loops:
            luv = l[uv_layer]
            
            #no elif or break because one face can have 1,2 or all 4 corners
            if AreVectorsQuasiEqual(luv.uv, lucv):
                lucf = face
            if AreVectorsQuasiEqual(luv.uv, ldcv):
                ldcf = face
            if AreVectorsQuasiEqual(luv.uv, rucv):
                rucf = face
            if AreVectorsQuasiEqual(luv.uv, rdcv):
                rdcf = face
                
    return lucf, ldcf, rucf, rdcf

def SetCornerFaces(uv_layer, lucv, ldcv, rucv, rdcv, lucf, ldcf, rucf, rdcf):
    
    SetCornerFaceByCorner(uv_layer, "leftUp", lucf, lucv) 
    SetCornerFaceByCorner(uv_layer, "leftDown", ldcf,ldcv)
    SetCornerFaceByCorner(uv_layer, "rightUp", rucf, rucv)
    SetCornerFaceByCorner(uv_layer, "rightDown", rdcf, rdcv)
    
    return lucf, ldcf, rucf, rdcf

def SetCornerFaceByCorner(uv_layer, side, face, corner):
    verts = []

    DeselectAll()

    #fill face verts to list
    for l in face.face.loops:
        luv = l[uv_layer]
        verts.append(luv.uv)
        luv.select = True
            
    if len(verts) is not 4:
        print("--error in determining a face")
        return

    rotatedFor = RotateSelCornerFaceUntilCornerIsHor(uv_layer, verts, side, corner)
    SetCornerFaceBy1Corner(face, verts, side, corner)
    RotateSelected(-rotatedFor)
    
    return

def RotateSelCornerFaceUntilCornerIsHor(uv_layer, verts, side, corner):
    rotations = 0
    #angle = 43.11 # 45-1 to break symetry and -1 not to restrict to even angles, +0.11 if the verts are really close, so it will stack up until they meet requirement
    angle = 43.11   
    allowedRotations = 200
        
    isHor = False
    while isHor is False:
        isHor = IsCornerOfCornerFaceHorizontal(uv_layer, verts, side, corner)
        
        if (rotations >=allowedRotations):
            print("exceeded max allowed rotations")
            return rotations*angle
            
        if isHor is False:   
            RotateSelected(angle)
            rotations = rotations +1
                         
    return rotations*angle

def IsCornerOfCornerFaceHorizontal(uv_layer, verts, side, corner):
    allowedError = 0.01
   
    if side is "leftUp" or side is "rightUp":
        firstHighest = verts[0]
        for v in verts:
            if (v.y > firstHighest.y):
                firstHighest = v
                
        secondHighest = verts[0]
        if AreVectorsQuasiEqual(firstHighest, secondHighest):
            secondHighest = verts[1]
        for v in verts:
            if AreVectorsQuasiEqual(v, firstHighest) is False:
                if(v.y > secondHighest.y):
                    secondHighest = v
                
        if abs(firstHighest.y - secondHighest.y) > allowedError:
            return False
        
        if AreVectorsQuasiEqual(corner, firstHighest):
            if side is "leftUp":
                if firstHighest.x < secondHighest.x:
                    return True
            if side is "rightUp":
                if firstHighest.x > secondHighest.x:
                    return True
                
        elif AreVectorsQuasiEqual(corner, secondHighest):
            if side is "leftUp":
                if secondHighest.x < firstHighest.x:
                    return True
            if side is "rightUp":
                if secondHighest.x > firstHighest.x:
                    return True
          
    elif side is "leftDown" or side is "rightDown":
        firstLowest = verts[0]
        for v in verts:
            if (v.y < firstLowest.y):
                firstLowest = v
        
        secondLowest = verts[0]
        if AreVectorsQuasiEqual(firstLowest, secondLowest):
            secondLowest = verts[1]
        for v in verts:
            if AreVectorsQuasiEqual(v, firstLowest) is False:
                if(v.y < secondLowest.y):
                    secondLowest = v
        
        if abs(firstLowest.y - secondLowest.y) > allowedError:
            return False
        
        if AreVectorsQuasiEqual(corner, firstLowest):
            if side is "leftDown":
                if firstLowest.x < secondLowest.x:
                    return True
            if side is "rightDown":
                if firstLowest.x > secondLowest.x:
                    return True
                
        elif AreVectorsQuasiEqual(corner, secondLowest):
            if side is "leftDown":
                if secondLowest.x < firstLowest.x:
                    return True
            if side is "rightDown":
                if secondLowest.x > firstLowest.x:
                    return True
                    
    return False

def SetCornerFaceBy1Corner(face, verts, side, corner):
    
    if side is "leftUp":
        for v in verts:
            if AreVectorsQuasiEqual(v, corner):
                face.leftUpVert = v
                verts.remove(v)
                break
        
        rightUpV = verts[0]
        for v in verts:
            if (v.y > rightUpV.y):
                rightUpV = v
        face.rightUpVert = rightUpV
        verts.remove(rightUpV)
        
        leftDownV = verts[0]
        for v in verts:
            if(v.x < leftDownV.x):
                leftDownV = v
        face.leftDownVert = leftDownV
        verts.remove(leftDownV)
        
        face.rightDownVert = verts[0]
        verts.remove(verts[0])

    elif side is "leftDown":
        for v in verts:
            if AreVectorsQuasiEqual(v, corner):
                face.leftDownVert = v
                verts.remove(v)
                break
        
        rightDownV = verts[0]
        for v in verts:
            if(v.y < rightDownV.y):
                rightDownV = v
        face.rightDownVert = rightDownV
        verts.remove(rightDownV)
        
        rightUpV = verts[0]
        for v in verts:
            if (v.x > rightUpV.x):
                rightUpV = v
        face.rightUpVert = rightUpV
        verts.remove(rightUpV)
        
        face.leftUpVert = verts[0]
        verts.remove(verts[0])
    
    elif side is "rightUp":
        for v in verts:
            if AreVectorsQuasiEqual(v, corner):
                face.rightUpVert = v
                verts.remove(v)
                break
        
        leftUpV = verts[0]
        for v in verts:
            if (v.y > leftUpV.y):
                leftUpV = v
        face.leftUpVert = leftUpV
        verts.remove(leftUpV)
        
        leftDownV = verts[0]
        for v in verts:
            if(v.x < leftDownV.x):
                leftDownV = v
        face.leftDownVert = leftDownV
        verts.remove(leftDownV)
        
        face.rightDownVert = verts[0]
        verts.remove(verts[0])

    elif side is "rightDown":
        for v in verts:
            if AreVectorsQuasiEqual(v, corner):
                face.rightDownVert = v
                verts.remove(v)
                break
        
        leftDownV = verts[0]
        for v in verts:
            if(v.y < leftDownV.y):
                leftDownV = v
        face.leftDownVert = leftDownV
        verts.remove(leftDownV)
        
        rightUpV = verts[0]
        for v in verts:
            if (v.x > rightUpV.x):
                rightUpV = v
        face.rightUpVert = rightUpV
        verts.remove(rightUpV)
        
        face.leftUpVert = verts[0]
        verts.remove(verts[0])
    
    return 

def SetFaceBy2Corners(uv_layer, side, face, corner1, corner2):
    if face is None:
        return
    
    DeselectAll()
    verts = []
    
    #select and append to verts
    for l in face.face.loops:
        luv = l[uv_layer]
        luv.select = True 
        verts.append(luv.uv)
        
    if side is "toRight":
        for v in verts:
            if AreVectorsQuasiEqual(v, corner1):
                face.leftUpVert = v
                verts.remove(v)
                break
        
        for v in verts:
            if AreVectorsQuasiEqual(v, corner2):
                face.leftDownVert = v
                verts.remove(v)
                break
        
        rotatedFor = RotateSelFaceUntil2CornersAreOnAxis(side, face.leftUpVert, face.leftDownVert)
        SetRestOfFaceBy2Corners(face, verts, side)
        RotateSelected(-rotatedFor)
    
    else:
        for v in verts:
            if AreVectorsQuasiEqual(v, corner1):
                face.leftUpVert = v
                verts.remove(v)
                break
        
        for v in verts:
            if AreVectorsQuasiEqual(v, corner2):
                face.rightUpVert = v
                verts.remove(v)
                break                 
                  
        rotatedFor = RotateSelFaceUntil2CornersAreOnAxis(side, face.leftUpVert, face.rightUpVert)
        SetRestOfFaceBy2Corners(face, verts, side)
        RotateSelected(-rotatedFor)
    
    return

def RotateSelFaceUntil2CornersAreOnAxis(side, corner1, corner2):
    rotations = 0
    #angle = 43.11 # 45-1 to break symetry and -1 not to restrict to even angles, +0.11 if the verts are really close, so it will stack up until they meet requirement
    angle = 43.11   
    totalAngle = 0
    allowedRotations = 200
    #if side is "lefUp" or side is "rightDown":
    #   angle = -angle
        
    isHor = False
    while isHor is False:
        
        isHor = Are2CornersOnAxis(side, corner1, corner2)
        
        rotations = rotations +1
        
        if (rotations >=allowedRotations):
            print("exceeded max allowed rotations")
            return rotations*angle
            
        if isHor is False:
            c1Xlessc2XBefore = corner1.x < corner2.x
            c1Ylessc2YBefore = corner1.y < corner2.y
            
            RotateSelected(angle)
            totalAngle = totalAngle + angle
            
            c1Xlessc2XAfter = corner1.x < corner2.x
            c1Ylessc2YAfter = corner1.y < corner2.y
            
            if side is "toRight":
                if (c1Xlessc2XBefore == c1Xlessc2XAfter) is False:
                    angle = -angle/2.0
            else:
                if (c1Ylessc2YBefore == c1Ylessc2YAfter) is False:
                    angle = -angle/2.0
                 
    return totalAngle

def Are2CornersOnAxis(side, corner1, corner2):
    allowedError = 0.01
 
    if side is "toRight":
        if abs(corner1.x - corner2.x) < allowedError:
            return True
    
    elif side is "toDown":
        if abs(corner1.y - corner2.y) < allowedError:
            return True
    
    return False

def SetRestOfFaceBy2Corners(face, verts, side):    
    if side is "toRight":
        if verts[0].y > verts[1].y:
            face.rightUpVert = verts[0]
            face.rightDownVert = verts[1]
        else:
            face.rightUpVert = verts[1]
            face.rightDownVert = verts[0]
        
        #if we got reversed situation
        if face.leftUpVert.y < face.leftDownVert.y:
            temp = face.rightUpVert
            face.rightUpVert = face.rightDownVert
            face.rightDownVert = temp
    
    else: 
        if verts[0].x < verts[1].x:
            face.leftDownVert = verts[0]
            face.rightDownVert = verts[1]
        else:
            face.leftDownVert = verts[1]
            face.rightDownVert = verts[0]
        
        if face.leftUpVert.x > face.rightUpVert.x:
            temp = face.leftDownVert
            face.leftDownVert = face.rightDownVert
            face.rightDownVert = temp
    return


def CursorClosestTo(verts, allowedError = 0.025):
    ratioX, ratioY = 255, 255
    for a in bpy.context.screen.areas:
        if a.type == 'IMAGE_EDITOR':
            img = a.spaces[0].image
            if img is not None and img.size[0] is not 0:
                ratioX, ratioY = img.size[0], img.size[1]
            break
    
    #any length that is certantly not smaller than distance of the closest
    min = 1000
    minV = verts[0]
    for v in verts:
        if v is None: continue
        for area in bpy.context.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                loc = area.spaces[0].cursor_location
                hyp = hypot(loc.x/ratioX -v.x, loc.y/ratioY -v.y)
                if (hyp < min):
                    min = hyp
                    minV = v
    
    if min is not 1000: return minV
    return None

def SetAll2dCursorsTo(x,y):
    last_area = bpy.context.area.type
    bpy.context.area.type = 'IMAGE_EDITOR'
   
    bpy.ops.uv.cursor_set(location=(x, y))

    bpy.context.area.type = last_area
    return

def RotateSelected(angle, pivot = None):
    if pivot is None:
        pivot = "MEDIAN"
   
    last_area = bpy.context.area.type
    bpy.context.area.type = 'IMAGE_EDITOR'
    
    last_pivot = bpy.context.space_data.pivot_point
    bpy.context.space_data.pivot_point = pivot
    
    for area in bpy.context.screen.areas:
        if area.type == 'IMAGE_EDITOR':
            #bpy.ops.transform.rotate({'pivot_point': pivot}, value=radians(angle), axis=(-0, -0, -1), constraint_axis=(False, False, False), constraint_orientation='LOCAL', mirror=False, proportional='DISABLED', proportional_edit_falloff='SMOOTH', proportional_size=1)
            bpy.ops.transform.rotate(value=radians(angle), axis=(-0, -0, -1), constraint_axis=(False, False, False), constraint_orientation='LOCAL', mirror=False, proportional='DISABLED', proportional_edit_falloff='SMOOTH', proportional_size=1)

            break

    bpy.context.space_data.pivot_point = last_pivot
    bpy.context.area.type = last_area
    
    return

def CountQuasiEqualVectors(v, list):
    i=0
    for e in list:
        if AreVectorsQuasiEqual(v,e):
            i += 1
    return i

def AreVectorsQuasiEqual(vect1, vect2, allowedError = 0.0001):
    if vect1 is None or vect2 is None:
        return False
    if abs(vect1.x -vect2.x) < allowedError and abs(vect1.y -vect2.y) < allowedError:
        return True
    return False

def ListsOfVerts(uv_layer, bm, startTime, allowedTime, precision):
    selVerts = []
    filteredVerts = []
    selFaces = []
    edgeFaces = []
    vertsDict = defaultdict(list)                #dict
    
    for f in bm.faces:
        isFaceSel = True
        isFaceContainSelV = False
        for l in f.loops:
            luv = l[uv_layer]
            if luv.select is False:
                isFaceSel = False
            else:
                isFaceContainSelV = True
                selVerts.append(luv.uv)
    
        if isFaceSel is True:
            if (time.clock() - startTime > allowedTime):
                print("time limit of", allowedTime,"exceeded while mapping verts.")
                return None        

            for l in f.loops:
                luv = l[uv_layer]
                
                x = round(luv.uv.x, precision)
                y = round(luv.uv.y, precision)
         
                vertsDict[(x, y)].append(luv)
            
            face = UvFace()
            face.face = f
            selFaces.append(face)
            continue
            
        if isFaceContainSelV:
            face = UvFace()
            face.face = f
            edgeFaces.append(face)
    
    [filteredVerts.append(v) for v in selVerts if CountQuasiEqualVectors(v, filteredVerts) is 0]
   
    return selVerts, filteredVerts, selFaces, edgeFaces, vertsDict

def RipUvFaces(uv_layer, bm):
    selFaces = []
    
    for f in bm.faces:
        isFaceSel = True
        for l in f.loops:
            luv = l[uv_layer]
            if luv.select is False:
                isFaceSel = False
                break
    
        if isFaceSel is True:
            selFaces.append(f)
    
    if len(selFaces) is 0:
        target = None
        for f in bm.faces:
            for l in f.loops:
                luv = l[uv_layer]
                if luv.select is True:
                    target = luv
                    break
            if target is not None: break
        
        for f in bm.faces:
            for l in f.loops:
                luv = l[uv_layer]
                luv.select = False
        
        target.select = True
        return

    DeselectAll()
    
    for sf in selFaces:
        for l in sf.loops:
            luv = l[uv_layer]
            luv.select = True
    
    return

def JoinUvFaces(uv_layer, bm, precision, selVerts, filteredVerts, vertsDict, radius = 0.02):
    for f in bm.faces:
        for l in f.loops:
           luv = l[uv_layer]
           if luv.select is True:
               x = round(luv.uv.x, precision)
               y = round(luv.uv.y, precision)
               vertsDict[(x,y)].append(luv)
         
    for key in vertsDict:
        min = 1000
        minV = None
        
        for f in bm.faces:
            for l in f.loops:
                luv = l[uv_layer]
                if luv.select is False:
                    hyp = hypot(vertsDict[(key[0], key[1])][0].uv.x -luv.uv.x, vertsDict[(key[0], key[1])][0].uv.y -luv.uv.y)
                    if (hyp <= min) and hyp < radius:
                        min = hyp
                        minV = luv
                        minV.select = True
        
            if min is not 1000:
                for v in vertsDict[(key[0], key[1])]:
                    v.uv.x = minV.uv.x
                    v.uv.y = minV.uv.y
    return

def DeselectAll():
    bpy.ops.uv.select_all(action='DESELECT')
    return

class UvFace():
    face = None
    leftUpVert = None
    leftDownVert = None
    rightUpVert = None
    rightDownVert = None
                
class UvSquares(bpy.types.Operator):
    """Reshapes UV faces to a grid of equivalent squares"""
    bl_idname = "uv.uv_squares"
    bl_label = "UVs to grid of squares"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main1(context, 0, False)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}

class UvGridByShape(bpy.types.Operator):
    """Reshapes UV faces to a grid with respect to shape by distance of verts of edges around selected corner"""
    bl_idname = "uv.uv_respect_shape"
    bl_label = "UVs to grid with respect to shape"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main1(context)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}
    
class SymUvGridByShape(bpy.types.Operator):
    """Same as UvGridByShape but in anti-clockwise direction"""
    bl_idname = "uv.sym_uv_respect_shape"
    bl_label = "UVs to grid in anti-clockwise with respect to shape"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main2(context)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}
    
class SymUvSquares(bpy.types.Operator):
    """Same as UvGridByShape just takes counter-clockwise direction for length"""
    bl_idname = "uv.sym_uv_squares"
    bl_label = "UVs to grid (sym - X)"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main2(context, False)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}

class RipFaces(bpy.types.Operator):
    """Rip UV faces apart"""
    bl_idname = "uv.uv_face_rip"
    bl_label = "UV face rip"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main3(context)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}

class JoinFaces(bpy.types.Operator):
    """Join selected UV faces to closest nonselected vertices"""
    bl_idname = "uv.uv_face_join"
    bl_label = "UV face join"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main4(context)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}
    
class SnapToAxis(bpy.types.Operator):
    """Snap sequenced vertices to Axis"""
    bl_idname = "uv.uv_snap_to_axis"
    bl_label = "UV snap vertices to axis"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main5(context)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}

class SnapToAxisWithEqual(bpy.types.Operator):
    """Snap sequenced vertices to Axis with Equal Distance between"""
    bl_idname = "uv.uv_snap_to_axis_and_equal"
    bl_label = "UV snap vertices to axis with equal distance between"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH')

    def execute(self, context):
        main6(context)
        bpy.ops.ed.undo_push()
        return {'FINISHED'}

addon_keymaps = []

def menu_func_uv_squares(self, context): self.layout.operator(UvSquares.bl_idname)
def menu_func_uv_grid_by_shape(self, context): self.layout.operator(UvGridByShape.bl_idname)
def menu_func_sym_uv_grid_by_shape(self, context): self.layout.operator(UvGridByShape.bl_idname)
def menu_func_sym_uv_squares(self, context): self.layout.operator(SymUvSquares.bl_idname)
def menu_func_face_rip(self, context): self.layout.operator(RipFaces.bl_idname)
def menu_func_face_join(self, context): self.layout.operator(JoinFaces.bl_idname)
    
class UvSquaresPanel(bpy.types.Panel):
    """UvSquares Panel"""
    bl_label = "UV Squares"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'TOOLS'

    def draw(self, context):
        layout = self.layout

        row = layout.row()
        row.label(text="Select Sequenced Vertices to:")
        split = layout.split()
        col = split.column(align=True)
        col.operator(SnapToAxis.bl_idname, text="Snap to Axis (X or Y)", icon = "ARROW_LEFTRIGHT")
        col.operator(SnapToAxisWithEqual.bl_idname, text="Snap with Equal Distance", icon = "ALIGN")
        
        row = layout.row()
        row.label(text="Convert \"Rectangle\" (4 corners):")
        split = layout.split()
        col = split.column(align=True)
        col.operator(UvGridByShape.bl_idname, text="To Grid By Shape", icon = "GRID")
        col.operator(UvSquares.bl_idname, text="To Square Grid", icon = "UV_FACESEL")
      
        split = layout.split()
        col = split.column(align=True)
        row = col.row(align=True)
        
        row.operator(SymUvGridByShape.bl_idname, text="Shape")
        row.operator(SymUvSquares.bl_idname, text="Square")
    
        row.label(text="anti", icon = "RECOVER_LAST")
        row.label(text="-cw")
        
      
        row = layout.row()
    
        row.label(text="Select Faces or Vertices to:")
        split = layout.split()
        col = split.column(align=True)
        row = col.row(align=True)
        
        row.operator(RipFaces.bl_idname, text="Rip Vertex", icon = "LAYER_ACTIVE")
        row.operator(RipFaces.bl_idname, text="Rip Faces", icon = "UV_ISLANDSEL")
        col.operator(JoinFaces.bl_idname, text="Snap to Closest Unselected", icon = "SNAP_INCREMENT")
        row = layout.row()
        row.label(text="V - Join (Stitch), I -Toggle Islands")
    

def register():
    bpy.utils.register_class(UvSquaresPanel)
    bpy.utils.register_class(UvSquares)
    bpy.utils.register_class(UvGridByShape)
    bpy.utils.register_class(SymUvGridByShape)
    bpy.utils.register_class(SymUvSquares)
    bpy.utils.register_class(RipFaces)
    bpy.utils.register_class(JoinFaces)
    bpy.utils.register_class(SnapToAxis)
    bpy.utils.register_class(SnapToAxisWithEqual)
	
    #menu
    bpy.types.IMAGE_MT_uvs.append(menu_func_uv_squares)
    bpy.types.IMAGE_MT_uvs.append(menu_func_uv_grid_by_shape)
    bpy.types.IMAGE_MT_uvs.append(menu_func_sym_uv_grid_by_shape)
    bpy.types.IMAGE_MT_uvs.append(menu_func_sym_uv_squares)
    bpy.types.IMAGE_MT_uvs.append(menu_func_face_rip)
    bpy.types.IMAGE_MT_uvs.append(menu_func_face_join)

    #handle the keymap
    wm = bpy.context.window_manager
    
    km = wm.keyconfigs.addon.keymaps.new(name='UV Editor', space_type='EMPTY')
    kmi = km.keymap_items.new(UvGridByShape.bl_idname, 'E', 'PRESS', alt=True)
    addon_keymaps.append((km, kmi))
    
    km = wm.keyconfigs.addon.keymaps.new(name='UV Editor', space_type='EMPTY')
    kmi = km.keymap_items.new(SymUvSquares.bl_idname, 'E', 'PRESS', alt=True, shift=True)
    addon_keymaps.append((km, kmi))
    
    km = wm.keyconfigs.addon.keymaps.new(name='UV Editor', space_type='EMPTY')
    kmi = km.keymap_items.new(RipFaces.bl_idname, 'V', 'PRESS', alt=True)
    addon_keymaps.append((km, kmi))
    
    km = wm.keyconfigs.addon.keymaps.new(name='UV Editor', space_type='EMPTY')
    kmi = km.keymap_items.new(JoinFaces.bl_idname, 'V', 'PRESS', alt=True, shift=True)
    addon_keymaps.append((km, kmi))
    
    

def unregister():
    bpy.utils.unregister_class(UvSquaresPanel)
    bpy.utils.unregister_class(UvSquares)
    bpy.utils.unregister_class(UvGridByShape)
    bpy.utils.unregister_class(SymUvGridByShape)
    bpy.utils.unregister_class(SymUvSquares)
    bpy.utils.unregister_class(RipFaces)
    bpy.utils.unregister_class(JoinFaces)
    bpy.utils.unregister_class(SnapToAxis)
    bpy.utils.unregister_class(SnapToAxisWithEqual)
    
    bpy.types.IMAGE_MT_uvs.remove(menu_func_uv_squares)
    bpy.types.IMAGE_MT_uvs.remove(menu_func_uv_grid_by_shape)
    bpy.types.IMAGE_MT_uvs.remove(menu_func_sym_uv_grid_by_shape)
    bpy.types.IMAGE_MT_uvs.remove(menu_func_sym_uv_squares)
    bpy.types.IMAGE_MT_uvs.remove(menu_func_face_rip)
    bpy.types.IMAGE_MT_uvs.remove(menu_func_face_join)
    
    # handle the keymap
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    # clear the list
    addon_keymaps.clear()

if __name__ == "__main__":
    register()
    





