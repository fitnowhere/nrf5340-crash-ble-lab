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

# A commit without an intervening source edit must also refresh the ID on
# incremental builds (including repositories whose .git is a worktree file).
execute_process(COMMAND git symbolic-ref -q HEAD
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  OUTPUT_VARIABLE CRASH_LAB_GIT_REF OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
foreach(git_path IN ITEMS HEAD "${CRASH_LAB_GIT_REF}")
  if(git_path)
    execute_process(COMMAND git rev-parse --git-path "${git_path}"
      WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
      OUTPUT_VARIABLE ref_path OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(ref_path)
      get_filename_component(ref_path "${ref_path}" ABSOLUTE
        BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
      if(EXISTS "${ref_path}")
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${ref_path}")
      endif()
    endif()
  endif()
endforeach()

# A source/config fingerprint travels in BLE status and metadata. Keep the
# unstripped ELF from this build: matching the fingerprint is necessary but
# not a substitute for archiving the precise ELF used to flash the target.
set(CRASH_LAB_INPUTS CMakeLists.txt Kconfig prj.conf sysbuild.conf
  boards/nrf5340dk_nrf5340_cpuapp.overlay
  src/main.c src/crashes.c src/dummy_ble.c src/dump_store.c src/flash_coredump.c
  src/diagnostics.c src/crashes.h src/dummy_ble.h src/dump_store.h src/diagnostics.h)
set(CRASH_LAB_FINGERPRINT "git:${CRASH_LAB_GIT_SHA}\n")
foreach(input IN LISTS CRASH_LAB_INPUTS)
  # A source edit must reconfigure, not just recompile: otherwise a different
  # ELF could advertise an old fingerprint and mis-symbolicate saved events.
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/${input}")
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
  src/diagnostics.c
)

# Preserve usable Cortex-M call frames in the unstripped ELF.
target_compile_options(app PRIVATE -fno-omit-frame-pointer -fno-optimize-sibling-calls)
