[app]
title = Touchless
project_dir = .
input_file = run_app.py
exec_directory = deployment_whisper
project_file = 
icon = assets/icons/touchless_icon.ico

[python]
python_path = C:\HGR App v1.0.0\.venv\Scripts\python.exe
packages = Nuitka==2.7.11
android_packages = buildozer==1.5.0,cython==0.29.33

[qt]
qml_files = 
excluded_qml_plugins = 
modules = Core,Gui,Multimedia,MultimediaWidgets,Widgets
plugins = accessiblebridge,egldeviceintegrations,generic,iconengines,imageformats,multimedia,platforminputcontexts,platforms,platformthemes,styles,xcbglintegrations

[android]
wheel_pyside = 
wheel_shiboken = 
plugins = 

[nuitka]
macos.permissions = 
mode = standalone
extra_args = --quiet --noinclude-qt-translations --include-package=hgr --include-data-dir=assets=assets --include-data-dir=GestureGuide=GestureGuide --include-data-dir=mp_modules=mediapipe/modules --include-data-dir=whisper_bundle=whisper.cpp --windows-console-mode=disable
recipe_dir = 
jars_dir = 
ndk_path = 
sdk_path = 
local_libs = 
arch = 

