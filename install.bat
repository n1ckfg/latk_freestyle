@echo off

set BUILD_TARGET=freestyle_to_gpencil.py
cd %cd%

copy %BUILD_TARGET% "%homepath%\AppData\Roaming\Blender Foundation\Blender\2.77\scripts\addons"
@pause