from freestyle.shaders import *
from freestyle.predicates import *
from freestyle.types import Operators, StrokeShader, StrokeVertex
from freestyle.chainingiterators import ChainSilhouetteIterator, ChainPredicateIterator
from freestyle.functions import *

import bpy
import bmesh
from bpy_extras import view3d_utils
import bpy_extras
from math import sqrt
import random
from mathutils import Vector, Matrix

bl_info = {
    "name": "Freestyle to Grease Pencil",
    "author": "Folkert de Vries",
    "version": (1, 0),
    "blender": (2, 74, 1),
    "location": "Properties > Render > Freestyle to Grease Pencil",
    "description": "Exports Freestyle's stylized to a Grease Pencil sketch",
    "warning": "",
    "wiki_url": "",
    "category": "Render",
    }

from bpy.props import (
        BoolProperty,
        EnumProperty,
        FloatProperty,
        IntProperty,
        PointerProperty,
        )
import parameter_editor


def get_strokes():
    # a tuple containing all strokes from the current render. should get replaced by freestyle.context at some point

    return tuple(map(Operators().get_stroke_from_index, range(Operators().get_strokes_size())))
# get the exact scene dimensions
def render_height(scene):
    return int(scene.render.resolution_y * scene.render.resolution_percentage / 100)

def render_width(scene):
    return int(scene.render.resolution_x * scene.render.resolution_percentage / 100)

def render_dimensions(scene):
    return render_width(scene), render_height(scene)

class FreestyleGPencil(bpy.types.PropertyGroup):
    """Implements the properties for the Freestyle to Grease Pencil exporter"""
    bl_idname = "RENDER_PT_gpencil_export"

    use_freestyle_gpencil_export = BoolProperty(
            name="Grease Pencil Export",
            description="Export Freestyle edges to Grease Pencil",
            )
    draw_mode = EnumProperty(
            name="Draw Mode",
            items=(
                # ('2DSPACE', "2D Space", "Export a single frame", 0),
                ('3DSPACE', "3D Space", "Export an animation", 1),
                # ('2DIMAGE', "2D Image", "", 2),
                ('SCREEN', "Screen", "", 3),
                ),
            default='3DSPACE',
            )
    use_fill = BoolProperty(
            name="Fill Contours",
            description="Fill the contour with the object's material color",
            )
    use_overwrite = BoolProperty(
            name="Overwrite Result",
            description="Remove the GPencil strokes from previous renders before a new render",
            default=True,
            )
    vertexHitbox = FloatProperty(
            name="Vertex Hitbox",
            description="How close a GP stroke needs to be to a vertex",
            default=0.2,
            )
    numColPlaces = IntProperty(
        name="Color places",
        description="How many decimal places colors are rounded to",
        default=5,
        )

class SVGExporterPanel(bpy.types.Panel):
    """Creates a Panel in the render context of the properties editor"""
    bl_idname = "RENDER_PT_FreestyleGPencilPanel"
    bl_space_type = 'PROPERTIES'
    bl_label = "Freestyle to Grease Pencil"
    bl_region_type = 'WINDOW'
    bl_context = "render"

    def draw_header(self, context):
        self.layout.prop(context.scene.freestyle_gpencil_export, "use_freestyle_gpencil_export", text="")

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        gp = scene.freestyle_gpencil_export
        freestyle = scene.render.layers.active.freestyle_settings

        layout.active = (gp.use_freestyle_gpencil_export and freestyle.mode != 'SCRIPT')

        row = layout.row()
        row.prop(gp, "draw_mode", expand=True)

        row = layout.row()
        #row.prop(svg, "split_at_invisible")
        # row.prop(gp, "use_fill")
        row.prop(gp, "use_overwrite")
        row.prop(gp, "vertexHitbox")

        row = layout.row()
        row.prop(gp, "numColPlaces")



def render_visible_strokes():
    """Renders the scene, selects visible strokes and returns them as a tuple"""
    upred = QuantitativeInvisibilityUP1D(0) # visible lines only
    #upred = TrueUP1D() # all lines
    Operators.select(upred)
    Operators.bidirectional_chain(ChainSilhouetteIterator(), NotUP1D(upred))
    Operators.create(TrueUP1D(), [])
    return get_strokes()

def render_external_contour():
    """Renders the scene, selects visible strokes of the Contour nature and returns them as a tuple"""
    upred = AndUP1D(QuantitativeInvisibilityUP1D(0), ContourUP1D())
    Operators.select(upred)
    # chain when the same shape and visible
    bpred = SameShapeIdBP1D()
    Operators.bidirectional_chain(ChainPredicateIterator(upred, bpred), NotUP1D(upred))
    Operators.create(TrueUP1D(), [])
    return get_strokes()


def create_gpencil_layer(scene, name, color, alpha, fill_color, fill_alpha):
    """Creates a new GPencil layer (if needed) to store the Freestyle result"""
    gp = bpy.data.grease_pencil.get("FreestyleGPencil", False) or bpy.data.grease_pencil.new(name="FreestyleGPencil")
    scene.grease_pencil = gp
    layer = gp.layers.get(name, False)
    if not layer:
        print("making new GPencil layer")
        layer = gp.layers.new(name=name, set_active=True)
        # set defaults
        '''
        layer.fill_color = fill_color
        layer.fill_alpha = fill_alpha
        layer.alpha = alpha 
        layer.color = color
        '''
    elif scene.freestyle_gpencil_export.use_overwrite:
        # empty the current strokes from the gp layer
        layer.clear()

    # can this be done more neatly? layer.frames.get(..., ...) doesn't seem to work
    frame = frame_from_frame_number(layer, scene.frame_current) or layer.frames.new(scene.frame_current)
    return layer, frame 

def frame_from_frame_number(layer, current_frame):
    """Get a reference to the current frame if it exists, else False"""
    return next((frame for frame in layer.frames if frame.frame_number == current_frame), False)

def freestyle_to_gpencil_strokes(strokes, frame, pressure=1, draw_mode='3DSPACE'):
    """Actually creates the GPencil structure from a collection of strokes"""
    mat = bpy.context.scene.camera.matrix_local.copy()
    # ~ ~ ~ ~ ~ ~ ~ 
    obj = bpy.context.scene.objects.active #bpy.context.edit_object
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me) #from_edit_mesh(me)
    #~
    # this speeds things up considerably
    images = getUvImages()
    #~
    uv_layer = bm.loops.layers.uv.active
    #~
    # ~ ~ ~ ~ ~ ~ ~ 
    for fstroke in strokes:
        # *** fstroke contains coordinates of original vertices ***
        # ~ ~ ~ ~ ~ ~ ~
        sampleVertRaw = (0,0,0)
        sampleVert = (0,0,0)
        #~
        '''
        fstrokeCounter = 0
        for svert in fstroke:
            fstrokeCounter += 1
        for i, svert in enumerate(fstroke):
            if (i == int(fstrokeCounter/2)):
            #if (i == fstrokeCounter-1):
                sampleVertRaw = mat * svert.point_3d
                break
        '''
        for svert in fstroke:
            sampleVertRaw = mat * svert.point_3d
            break
        sampleVert = (sampleVertRaw[0], sampleVertRaw[1], sampleVertRaw[2])
        #~
        pixel = (1,0,1)
        lastPixel = getActiveColor().color
        # TODO better hit detection method needed
        # possibly sort original verts by distance?
        # http://stackoverflow.com/questions/6618515/sorting-list-based-on-values-from-another-list
        # X.sort(key=dict(zip(X, Y)).get)
        for v in bm.verts:
            #if (compareTuple(obj.matrix_world * v.co, obj.matrix_world * v.co, numPlaces=1) == True):
            if (hitDetect3D(obj.matrix_world * v.co, sampleVert, hitbox=bpy.context.scene.freestyle_gpencil_export.vertexHitbox) == True):
            #if (getDistance(obj.matrix_world * v.co, sampleVert) <= 0.5):
                uv_first = uv_from_vert_first(uv_layer, v)
                #uv_average = uv_from_vert_average(uv_layer, v)
                #print("Vertex: %r, uv_first=%r, uv_average=%r" % (v, uv_first, uv_average))
                #~
                pixelRaw = getPixelFromUvArray(images[obj.active_material.texture_slots[0].texture.image.name], uv_first[0], uv_first[1])
                #pixelRaw = getPixelFromUv(obj.active_material.texture_slots[0].texture.image, uv_first[0], uv_first[1])
                #pixelRaw = getPixelFromUv(obj.active_material.texture_slots[0].texture.image, uv_average[0], uv_average[1])
                pixel = (pixelRaw[0], pixelRaw[1], pixelRaw[2])
                break
                #print("Pixel: " + str(pixel))    
            else:
                pixel = lastPixel   
        # ~ ~ ~ ~ ~ ~ ~ 
        #try:
        createColor(pixel, bpy.context.scene.freestyle_gpencil_export.numColPlaces)
        #except:
            #pass
        gpstroke = frame.strokes.new(getActiveColor().name)
        # enum in ('SCREEN', '3DSPACE', '2DSPACE', '2DIMAGE')
        gpstroke.draw_mode = draw_mode
        gpstroke.points.add(count=len(fstroke))

        if draw_mode == '3DSPACE':
            for svert, point in zip(fstroke, gpstroke.points):
                # svert.attribute.color = (1, 0, 0) # confirms that this callback runs earlier than the shading
                point.co = mat * svert.point_3d
                point.select = True
                point.strength = 1
                point.pressure = pressure
        elif draw_mode == 'SCREEN':
            width, height = render_dimensions(bpy.context.scene)
            for svert, point in zip(fstroke, gpstroke.points):
                x, y = svert.point
                point.co = Vector((abs(x / width), abs(y / height), 0.0)) * 100
                point.select = True
                point.strength = 1
                point.pressure = 1
        else:
            raise NotImplementedError()

# ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~
# ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~

# http://blender.stackexchange.com/questions/49341/how-to-get-the-uv-corresponding-to-a-vertex-via-the-python-api
# https://blenderartists.org/forum/archive/index.php/t-195230.html
# https://developer.blender.org/T28211
# http://blenderscripting.blogspot.ca/2012/08/adjusting-image-pixels-walkthrough.html
# https://www.blender.org/forum/viewtopic.php?t=25804
# https://docs.blender.org/api/blender_python_api_2_63_2/bmesh.html
# http://blender.stackexchange.com/questions/1311/how-can-i-get-vertex-positions-from-a-mesh

def uv_from_vert_first(uv_layer, v):
    for l in v.link_loops:
        uv_data = l[uv_layer]
        return uv_data.uv
    return None


def uv_from_vert_average(uv_layer, v):
    uv_average = Vector((0.0, 0.0))
    total = 0.0
    for loop in v.link_loops:
        uv_average += loop[uv_layer].uv
        total += 1.0
    #~
    if total != 0.0:
        return uv_average * (1.0 / total)
    else:
        return None

# Example using the functions above
def testUvs():
    obj = bpy.context.scene.objects.active #edit_object
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me) #from_edit_mesh(me)
    #~
    images = getUvImages()
    #~
    uv_layer = bm.loops.layers.uv.active
    #~
    for v in bm.verts:
        uv_first = uv_from_vert_first(uv_layer, v)
        uv_average = uv_from_vert_average(uv_layer, v)
        print("Vertex: %r, uv_first=%r, uv_average=%r" % (v, uv_first, uv_average))
        #~
        pixel = getPixelFromUvArray(images[obj.active_material.texture_slots[0].texture.image.name], uv_first[0], uv_first[1])
        print("Pixel: " + str(pixel))

def getVerts(target=None):
    if not target:
        target = bpy.context.scene.objects.active
    me = target.data
    bm = bmesh.new()
    bm.from_mesh(me)
    return bm.verts

def getUvImages():
    obj = bpy.context.scene.objects.active
    uv_images = {}
    #~
    #for uv_tex in obdata.uv_textures.active.data:
    for tex in obj.active_material.texture_slots:
        try:
            uv_tex = tex.texture
            if (uv_tex.image and
                uv_tex.image.name not in uv_images and
                uv_tex.image.pixels):

                uv_images[uv_tex.image.name] = (
                    uv_tex.image.size[0],
                    uv_tex.image.size[1],
                    uv_tex.image.pixels[:]
                    # Accessing pixels directly is far too slow.
                    # Copied to new array for massive performance-gain.
                )
        except:
            pass
    #~
    return uv_images

def getPixelFromImage(img, xPos, yPos):
    imgWidth = int(img.size[0])
    r = img.pixels[4 * (xPos + imgWidth * yPos) + 0]
    g = img.pixels[4 * (xPos + imgWidth * yPos) + 1]
    b = img.pixels[4 * (xPos + imgWidth * yPos) + 2]
    a = img.pixels[4 * (xPos + imgWidth * yPos) + 3]
    return [r, g, b, a]

def getPixelFromUv(img, u, v):
    imgWidth = int(img.size[0])
    imgHeight = int(img.size[1])
    pixel_x = int(u * imgWidth)
    pixel_y = int(v * imgHeight)
    return getPixelFromImage(img, pixel_x, pixel_y)

# *** these methods are much faster but don't work in all contexts
def getPixelFromImageArray(img, xPos, yPos):
    imgWidth = int(img[0]) #img.size[0]
    #r = img.pixels[4 * (xPos + imgWidth * yPos) + 0]
    r = img[2][4 * (xPos + imgWidth * yPos) + 0]
    g = img[2][4 * (xPos + imgWidth * yPos) + 1]
    b = img[2][4 * (xPos + imgWidth * yPos) + 2]
    a = img[2][4 * (xPos + imgWidth * yPos) + 3]
    return [r, g, b, a]

def getPixelFromUvArray(img, u, v):
    imgWidth = int(img[0]) #img.size[0]
    imgHeight = int(img[1]) #img.size[1]
    pixel_x = int(u * imgWidth)
    pixel_y = int(v * imgHeight)
    return getPixelFromImageArray(img, pixel_x, pixel_y)

def hitDetect3D(p1, p2, hitbox=0.01):
    if (p1[0] + hitbox >= p2[0] - hitbox and p1[0] - hitbox <= p2[0] + hitbox and p1[1] + hitbox >= p2[1] - hitbox and p1[1] - hitbox <= p2[1] + hitbox and p1[2] + hitbox >= p2[2] - hitbox and p1[2] - hitbox <= p2[2] + hitbox):
        return True
    else:
        return False

def getDistance(v1, v2):
    return sqrt((v1[0] - v2[0])**2 + (v1[1] - v2[1])**2 + (v1[2] - v2[2])**2)

# ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~
# ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~

def getActiveGp(_name="GPencil"):
    try:
        pencil = bpy.context.scene.grease_pencil
    except:
        pencil = None
    try:
        gp = bpy.data.grease_pencil[pencil.name]
    except:
        gp = bpy.data.grease_pencil.new(_name)
        bpy.context.scene.grease_pencil = gp
    print("Active GP block is: " + gp.name)
    return gp

def getActivePalette():
    gp = getActiveGp()
    palette = gp.palettes.active
    if (palette == None):
        palette = gp.palettes.new(gp.name + "_Palette", set_active = True)
    if (len(palette.colors) < 1):
        color = palette.colors.new()
        color.color = (0,0,0)
    print("Active palette is: " + gp.palettes.active.name)
    return palette

def getActiveColor():
    palette = getActivePalette()
    print("Active color is: " + "\"" + palette.colors.active.name + "\" " + str(palette.colors.active.color))
    return palette.colors.active

def getActiveLayer():
    gp = getActiveGp()
    layer = gp.layers.active
    return layer

def createPoint(_stroke, _index, _point, pressure=1, strength=1):
    _stroke.points[_index].co = _point
    _stroke.points[_index].select = True
    _stroke.points[_index].pressure = pressure
    _stroke.points[_index].strength = strength

def createColor(_color, numPlaces=7):
    #frame = getActiveFrame()
    palette = getActivePalette()
    matchingColorIndex = -1
    places = numPlaces
    for i in range(0, len(palette.colors)):
        if (roundVal(_color[0], places) == roundVal(palette.colors[i].color.r, places) and roundVal(_color[1], places) == roundVal(palette.colors[i].color.g, places) and roundVal(_color[2], places) == roundVal(palette.colors[i].color.b, places)):
            matchingColorIndex = i
    #~
    if (matchingColorIndex == -1):
        color = palette.colors.new()
        color.color = _color
    else:
        palette.colors.active = palette.colors[matchingColorIndex]
        color = palette.colors[matchingColorIndex]
    #~        
    print("Active color is: " + "\"" + palette.colors.active.name + "\" " + str(palette.colors.active.color))
    return color

def compareTuple(t1, t2, numPlaces=5):
    if (roundVal(t1[0], numPlaces) == roundVal(t2[0], numPlaces) and roundVal(t1[1], numPlaces) == roundVal(t2[1], numPlaces) and roundVal(t1[2], numPlaces) == roundVal(t2[2], numPlaces)):
        return True
    else:
        return False

def roundVal(a, b):
    formatter = "{0:." + str(b) + "f}"
    return formatter.format(a)

def roundValInt(a):
    formatter = "{0:." + str(0) + "f}"
    return int(formatter.format(a))

# ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~
# ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~

def freestyle_to_fill(scene):
    default = dict(color=(0, 0, 0), alpha=1, fill_color=(0, 1, 0), fill_alpha=1)
    layer, frame = create_gpencil_layer(scene, "freestyle fill", **default)
    # render the external contour 
    strokes = render_external_contour()
    freestyle_to_gpencil_strokes(strokes, frame, draw_mode=scene.freestyle_gpencil_export.draw_mode)

def freestyle_to_strokes(scene):
    default = dict(color=(0, 0, 0), alpha=1, fill_color=(0, 1, 0), fill_alpha=0)
    layer, frame = create_gpencil_layer(scene, "freestyle stroke", **default)
    # render the normal strokes 
    #strokes = render_visible_strokes()
    strokes = get_strokes()
    freestyle_to_gpencil_strokes(strokes, frame, draw_mode=scene.freestyle_gpencil_export.draw_mode)


classes = (
    FreestyleGPencil,
    SVGExporterPanel,
    )

def export_stroke(scene, _, x):
    # create stroke layer
    freestyle_to_strokes(scene)

def export_fill(scene, layer, lineset):
    # Doesn't work for 3D due to concave edges
    return

    #if not scene.freestyle_gpencil_export.use_freestyle_gpencil_export:
    #    return 

    #if scene.freestyle_gpencil_export.use_fill:
    #    # create the fill layer
    #    freestyle_to_fill(scene)
    #    # delete these strokes
    #    Operators.reset(delete_strokes=True)



def register():

    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.freestyle_gpencil_export = PointerProperty(type=FreestyleGPencil)

    parameter_editor.callbacks_lineset_pre.append(export_fill)
    parameter_editor.callbacks_lineset_post.append(export_stroke)
    # bpy.app.handlers.render_post.append(export_stroke)
    print("anew")

def unregister():

    for cls in classes:
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.freestyle_gpencil_export

    parameter_editor.callbacks_lineset_pre.append(export_fill)
    parameter_editor.callbacks_lineset_post.remove(export_stroke)


if __name__ == '__main__':
    register()

