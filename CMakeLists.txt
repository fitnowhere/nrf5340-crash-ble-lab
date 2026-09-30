cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(nrf5340_crash_ble_lab)

execute_process(
  COMMAND git rev-parse --short=12 HEAD
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  OUTPUT_VARIABLE CRASH_LAB_GIT_SHA
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
)
if(NOT CRASH_LAB_GIT_SHA)
  set(CRASH_LAB_GIT_SHA "uncommitted")
endif()

# A source/config fingerprint travels in BLE status and metadata. Keep the
# unstripped ELF from this build: matching the fingerprint is necessary but
# not a substitute for archiving the precise ELF used to flash the target.
set(CRASH_LAB_INPUTS CMakeLists.txt Kconfig prj.conf sysbuild.conf
  boards/nrf5340dk_nrf5340_cpuapp.overlay
  src/main.c src/crashes.c src/dummy_ble.c src/dump_store.c src/flash_coredump.c
  src/crashes.h src/dummy_ble.h src/dump_store.h)
set(CRASH_LAB_FINGERPRINT "git:${CRASH_LAB_GIT_SHA}\n")
foreach(input IN LISTS CRASH_LAB_INPUTS)
  file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/${input}" digest)
  string(APPEND CRASH_LAB_FINGERPRINT "${input}:${digest}\n")
endforeach()
if(EXISTS "${CMAKE_CURRENT_BINARY_DIR}/zephyr/.config")
  file(SHA256 "${CMAKE_CURRENT_BINARY_DIR}/zephyr/.config" config_digest)
  string(APPEND CRASH_LAB_FINGERPRINT "config:${config_digest}\n")
endif()
string(SHA256 CRASH_LAB_BUILD_FINGERPRINT "${CRASH_LAB_FINGERPRINT}")
string(SUBSTRING "${CRASH_LAB_BUILD_FINGERPRINT}" 0 20 CRASH_LAB_BUILD_FINGERPRINT)

configure_file(src/build_id.h.in ${CMAKE_CURRENT_BINARY_DIR}/generated/build_id.h @ONLY)
target_include_directories(app PRIVATE src ${CMAKE_CURRENT_BINARY_DIR}/generated)

target_sources(app PRIVATE
  src/main.c
  src/crashes.c
  src/dummy_ble.c
  src/dump_store.c
  src/flash_coredump.c
)

# Preserve usable Cortex-M call frames in the unstripped ELF.
target_compile_options(app PRIVATE -fno-omit-frame-pointer -fno-optimize-sibling-calls)
