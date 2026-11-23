cmake_minimum_required(VERSION 3.28.0...3.28.0 FATAL_ERROR)

project(SlicerFlow)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_CATEGORY "Utilities")
set(EXTENSION_HOMEPAGE "https://github.com/Talk2View/SlicerFlow")
set(EXTENSION_CONTRIBUTORS "Andy Huynh (A2B Technology Corporation Pty Ltd)")
set(EXTENSION_DESCRIPTION "Chat with an AI assistant that can look at and change your 3D Slicer scene: load data, set views, threshold and measure segments. It runs on the Talk2View online service and sends nothing until you start the chat; not for clinical use.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/Talk2View/SlicerFlow/main/SlicerFlow.png")
set(EXTENSION_SCREENSHOTURLS "https://raw.githubusercontent.com/Talk2View/SlicerFlow/main/Screenshots/1.png")
set(EXTENSION_DEPENDS "NA") # Specified as a list or "NA" if no dependencies

#-----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
# Extension modules
add_subdirectory(SlicerFlow)
## NEXT_MODULE

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
