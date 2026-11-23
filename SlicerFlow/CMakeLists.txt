#-----------------------------------------------------------------------------
set(MODULE_NAME SlicerFlow)

#-----------------------------------------------------------------------------
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  ${MODULE_NAME}Lib/__init__.py
  ${MODULE_NAME}Lib/audio.py
  ${MODULE_NAME}Lib/chat_dock.py
  ${MODULE_NAME}Lib/config.py
  ${MODULE_NAME}Lib/followup.py
  ${MODULE_NAME}Lib/host.py
  ${MODULE_NAME}Lib/lifecycle.py
  ${MODULE_NAME}Lib/panel_style.py
  ${MODULE_NAME}Lib/paths.py
  ${MODULE_NAME}Lib/perf.py
  ${MODULE_NAME}Lib/prompt.py
  ${MODULE_NAME}Lib/runtime.py
  ${MODULE_NAME}Lib/sdk.py
  ${MODULE_NAME}Lib/skills.py
  ${MODULE_NAME}Lib/tools_panel.py
  ${MODULE_NAME}Lib/user_tools.py
  ${MODULE_NAME}Lib/tools/__init__.py
  ${MODULE_NAME}Lib/tools/_nodes.py
  ${MODULE_NAME}Lib/tools/capture.py
  ${MODULE_NAME}Lib/tools/markups.py
  ${MODULE_NAME}Lib/tools/scene.py
  ${MODULE_NAME}Lib/tools/segmentation.py
  ${MODULE_NAME}Lib/tools/view.py
  ${MODULE_NAME}Lib/tools/volume.py
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Fonts/OFL.txt
  Resources/Fonts/SpaceGrotesk-Bold.ttf
  Resources/Fonts/SpaceGrotesk-Regular.ttf
  Resources/Fonts/SpaceGrotesk-SemiBold.ttf
  Resources/Icons/${MODULE_NAME}.png
  Resources/Icons/Talk2ViewLogomark.png
  Resources/partner.json
  Resources/Skills/ct-window-presets/SKILL.md
  Resources/Skills/explore-sample-data/SKILL.md
  Resources/Skills/measure-and-report-volumes/SKILL.md
  Resources/Skills/segment-by-threshold/SKILL.md
  )

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )

#-----------------------------------------------------------------------------
if(BUILD_TESTING)
  # Register the unittest subclass in the main script as a ctest.
  slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)
endif()
