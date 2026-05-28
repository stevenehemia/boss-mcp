cmake_minimum_required(VERSION 3.10)
project(boss-mcp)

set(CMAKE_CXX_STANDARD 17)

add_executable(boss_mcp
	src/main.cpp
	src/transport.cpp
	src/logger.cpp
	src/handlers.cpp
	src/expression.cpp
)

target_include_directories(boss_mcp PRIVATE
	src
	external/json/include
	../Source
)

set(BOSS_SHARED_LIB "${CMAKE_CURRENT_LIST_DIR}/../build/deps/lib/libBOSS.so")
if(NOT EXISTS ${BOSS_SHARED_LIB})
	message(FATAL_ERROR "Missing shared library: ${BOSS_SHARED_LIB}")
endif()
target_link_libraries(boss_mcp PRIVATE ${BOSS_SHARED_LIB})

if(UNIX AND NOT APPLE)
	set_target_properties(boss_mcp PROPERTIES
		BUILD_RPATH "$ORIGIN/../build/deps/lib"
		INSTALL_RPATH "$ORIGIN/../build/deps/lib"
	)
endif()

if(UNIX AND NOT APPLE)
	target_link_libraries(boss_mcp PRIVATE dl)
endif()