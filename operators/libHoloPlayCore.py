# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import bpy
import gpu
from gpu_extras.presets import draw_texture_2d
import json
import subprocess
import logging
import os
import ctypes
from bgl import *
from math import *
from mathutils import *
from bpy.types import AddonPreferences, PropertyGroup
from bpy.props import FloatProperty, PointerProperty
from ctypes.util import find_library
import numpy as np
import time
from enum import Enum 


# -------------------- Load Library ----------------------
# Load the HoloPlay Core SDK Library
print("Loading HoloPlay Core SDK library")
hpc = ctypes.cdll.LoadLibrary(find_library('HoloPlayCore'))




# ---------------------- Constants -----------------------


# hpc_client_error
###################
#   Enum definition for errors returned from the HoloPlayCore dynamic library.
#    
#   This encapsulates potential errors with the connection itself,
#   as opposed to hpc_service_error, which describes potential error messages
#   included in a successful reply from HoloPlay Service.

class client_error(Enum):
    CLIERR_NOERROR = 0
    CLIERR_NOSERVICE = 1
    CLIERR_VERSIONERR = 2
    CLIERR_SERIALIZEERR = 3
    CLIERR_DESERIALIZEERR = 4
    CLIERR_MSGTOOBIG = 5
    CLIERR_SENDTIMEOUT = 6
    CLIERR_RECVTIMEOUT = 7
    CLIERR_PIPEERROR = 8
    CLIERR_APPNOTINITIALIZED = 9


# hpc_service_error
###################
#   Enum definition for error codes included in HoloPlay Service responses.
#
#   Most error messages from HoloPlay Service concern access to the HoloPlay Service
#   internal renderer, which is supported but not the primary focus of the current
#   version of HoloPlay Core.
#        
#   Future versions of HoloPlay Service may return error codes not defined by this
#   spec.

class service_error(Enum):
    ERR_NOERROR = 0
    ERR_BADCBOR = 1
    ERR_BADCOMMAND = 2
    ERR_NOIMAGE = 3
    ERR_LKGNOTFOUND = 4
    ERR_NOTINCACHE = 5
    ERR_INITTOOLATE = 6
    ERR_NOTALLOWED = 7	
	

# hpc_license_type
###################
#   Enum definition for possible types of licenses associated with a HoloPlay Core app.
#       
#   Non-commercial apps can't run on Looking Glass devices without an associated commercial license.

class license_type(Enum):
    LICENSE_NONCOMMERCIAL = 0
    LICENSE_COMMERCIAL = 1
	


# ---------- PYTHON WRAPPE FOR HOLOPLAYCORE -------------
# Make the function names visible at the module level and add types

LightfieldVertShaderGLSL = '''
layout (location = 0)
in vec2 vertPos_data;
out vec2 texCoords;
void main()
{
    gl_Position = vec4(vertPos_data.xy, 0.0, 1.0);
    texCoords = (vertPos_data.xy + 1.0) * 0.5;
}
'''

LightfieldFragShaderGLSL = '''
in vec2 texCoords;
out vec4 fragColor;

// Calibration values
uniform float pitch;
uniform float tilt;
uniform float center;
uniform int invView;
uniform float subp;
uniform float displayAspect;
uniform int ri;
uniform int bi;

// Quilt settings
uniform vec3 tile;
uniform vec2 viewPortion;
uniform float quiltAspect;
uniform int overscan;
uniform int quiltInvert;

uniform int debug;

uniform sampler2D screenTex;

vec2 texArr(vec3 uvz)
{
    // decide which section to take from based on the z.
    float z = floor(uvz.z * tile.z);
    float x = (mod(z, tile.x) + uvz.x) / tile.x;
    float y = (floor(z / tile.x) + uvz.y) / tile.y;
    return vec2(x, y) * viewPortion.xy;
}

// recreate CG clip function (clear pixel if any component is negative)
void clip(vec3 toclip)
{
    // "discard" cancels the drawing of the current pixel
    if (any(lessThan(toclip, vec3(0,0,0)))) discard;
}

void main()
{
    if (debug == 1)
    {
        fragColor = texture(screenTex, texCoords.xy);
    }
    else {
        float invert = 1.0;
        if (invView + quiltInvert == 1) invert = -1.0;
        vec3 nuv = vec3(texCoords.xy, 0.0);
        nuv -= 0.5;
        float modx = clamp (step(quiltAspect, displayAspect) * step(float(overscan), 0.5) + step(displayAspect, quiltAspect) * step(0.5, float(overscan)), 0, 1);
        nuv.x = modx * nuv.x * displayAspect / quiltAspect + (1.0-modx) * nuv.x;
        nuv.y = modx * nuv.y + (1.0-modx) * nuv.y * quiltAspect / displayAspect;
        nuv += 0.5;
        clip (nuv);
        clip (1.0-nuv);
        vec4 rgb[3];
        for (int i=0; i < 3; i++)
        {
            nuv.z = (texCoords.x + i * subp + texCoords.y * tilt) * pitch - center;
            nuv.z = mod(nuv.z + ceil(abs(nuv.z)), 1.0);
            nuv.z *= invert;
            rgb[i] = texture(screenTex, texArr(nuv));
        }
        fragColor = vec4(rgb[ri].r, rgb[1].g, rgb[bi].b, 1.0);
    }
}
'''

# ----------------- GENERAL FUNCTIONS -------------------
# int hpc_InitializeApp(const char *app_name, int license)
InitializeApp = hpc.hpc_InitializeApp
InitializeApp.argtypes = [ctypes.c_char_p, ctypes.c_int]
InitializeApp.restype = ctypes.c_int

# int hpc_RefreshState()
RefreshState = hpc.hpc_RefreshState
RefreshState.argtypes = None
RefreshState.restype = ctypes.c_int

# int hpc_CloseApp()
CloseApp = hpc.hpc_CloseApp
CloseApp.argtypes = None
CloseApp.restype = ctypes.c_int

# int hpc_GetHoloPlayCoreVersion(const char *buffer, int bufferSize)
GetHoloPlayCoreVersion = hpc.hpc_GetHoloPlayCoreVersion
GetHoloPlayCoreVersion.argtypes = [ctypes.c_char_p, ctypes.c_int]
GetHoloPlayCoreVersion.restype = ctypes.c_int

# int hpc_GetHoloPlayServiceVersion(const char *buffer, int bufferSize)
GetHoloPlayServiceVersion = hpc.hpc_GetHoloPlayServiceVersion
GetHoloPlayServiceVersion.argtypes = [ctypes.c_char_p, ctypes.c_int]
GetHoloPlayServiceVersion.restype = ctypes.c_int

# int hpc_GetNumDevices()
GetNumDevices = hpc.hpc_GetNumDevices
GetNumDevices.argtypes = None
GetNumDevices.restype = ctypes.c_int


# ----------------- DEVICE PROPERTIES ------------------
# int hpc_GetDeviceHDMIName(int DEV_INDEX, const char *buffer, int bufferSize)
GetDeviceHDMIName = hpc.hpc_GetDeviceHDMIName
GetDeviceHDMIName.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
GetDeviceHDMIName.restype = ctypes.c_int

# int hpc_GetDeviceType(int DEV_INDEX, const char *buffer, int bufferSize)
GetDeviceType = hpc.hpc_GetDeviceType
GetDeviceType.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
GetDeviceType.restype = ctypes.c_int

# int hpc_GetDevicePropertyWinX(int DEV_INDEX)
GetDevicePropertyWinX = hpc.hpc_GetDevicePropertyWinX
GetDevicePropertyWinX.argtypes = [ctypes.c_int]
GetDevicePropertyWinX.restype = ctypes.c_int

# int hpc_GetDevicePropertyWinY(int DEV_INDEX)
GetDevicePropertyWinY = hpc.hpc_GetDevicePropertyWinY
GetDevicePropertyWinY.argtypes = [ctypes.c_int]
GetDevicePropertyWinY.restype = ctypes.c_int

# int hpc_GetDevicePropertyScreenW(int DEV_INDEX)
GetDevicePropertyScreenW = hpc.hpc_GetDevicePropertyScreenW
GetDevicePropertyScreenW.argtypes = [ctypes.c_int]
GetDevicePropertyScreenW.restype = ctypes.c_int

# int hpc_GetDevicePropertyScreenH(int DEV_INDEX)
GetDevicePropertyScreenH = hpc.hpc_GetDevicePropertyScreenH
GetDevicePropertyScreenH.argtypes = [ctypes.c_int]
GetDevicePropertyScreenH.restype = ctypes.c_int

# float hpc_GetDevicePropertyDisplayAspect(int DEV_INDEX)
GetDevicePropertyDisplayAspect = hpc.hpc_GetDevicePropertyDisplayAspect
GetDevicePropertyDisplayAspect.argtypes = [ctypes.c_int]
GetDevicePropertyDisplayAspect.restype = ctypes.c_float

# float hpc_GetDevicePropertyPitch(int DEV_INDEX)
GetDevicePropertyPitch = hpc.hpc_GetDevicePropertyPitch
GetDevicePropertyPitch.argtypes = [ctypes.c_int]
GetDevicePropertyPitch.restype = ctypes.c_float

# float hpc_GetDevicePropertyTilt(int DEV_INDEX)
GetDevicePropertyTilt = hpc.hpc_GetDevicePropertyTilt
GetDevicePropertyTilt.argtypes = [ctypes.c_int]
GetDevicePropertyTilt.restype = ctypes.c_float

# float hpc_GetDevicePropertyCenter(int DEV_INDEX)
GetDevicePropertyCenter = hpc.hpc_GetDevicePropertyCenter
GetDevicePropertyCenter.argtypes = [ctypes.c_int]
GetDevicePropertyCenter.restype = ctypes.c_float

# float hpc_GetDevicePropertySubp(int DEV_INDEX)
GetDevicePropertySubp = hpc.hpc_GetDevicePropertySubp
GetDevicePropertySubp.argtypes = [ctypes.c_int]
GetDevicePropertySubp.restype = ctypes.c_float

# float hpc_GetDevicePropertyFringe(int DEV_INDEX)
GetDevicePropertyFringe = hpc.hpc_GetDevicePropertyFringe
GetDevicePropertyFringe.argtypes = [ctypes.c_int]
GetDevicePropertyFringe.restype = ctypes.c_float

# int hpc_GetDevicePropertyRi(int DEV_INDEX)
GetDevicePropertyRi = hpc.hpc_GetDevicePropertyRi
GetDevicePropertyRi.argtypes = [ctypes.c_int]
GetDevicePropertyRi.restype = ctypes.c_int

# int hpc_GetDevicePropertyBi(int DEV_INDEX)
GetDevicePropertyBi = hpc.hpc_GetDevicePropertyBi
GetDevicePropertyBi.argtypes = [ctypes.c_int]
GetDevicePropertyBi.restype = ctypes.c_int

# int hpc_GetDevicePropertyInvView(int DEV_INDEX)
GetDevicePropertyInvView = hpc.hpc_GetDevicePropertyInvView
GetDevicePropertyInvView.argtypes = [ctypes.c_int]
GetDevicePropertyInvView.restype = ctypes.c_int



# --------------------- VIEW CONE ----------------------
# float viewCone = hpc_GetDevicePropertyFloat(int DEV_INDEX, c_char_p ViewCone)
GetDevicePropertyFloat = hpc.hpc_GetDevicePropertyFloat
GetDevicePropertyFloat.argtypes = [ctypes.c_int, ctypes.c_char_p]
GetDevicePropertyFloat.restype = ctypes.c_float
