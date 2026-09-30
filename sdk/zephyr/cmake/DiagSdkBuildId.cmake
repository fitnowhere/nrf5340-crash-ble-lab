# A caller-owned, source-content 20-character firmware fingerprint. This is a
# mismatch guard, not a cryptographic identity for the final linked ELF.
function(diag_sdk_fingerprint output_variable)
  cmake_parse_arguments(DIAG "" "" "SOURCES" ${ARGN})
  if(NOT DIAG_SOURCES)
    message(FATAL_ERROR "diag_sdk_fingerprint needs SOURCES with absolute paths")
  endif()

  execute_process(COMMAND git rev-parse --short=12 HEAD
    WORKING_DIRECTORY "${APPLICATION_SOURCE_DIR}"
    OUTPUT_VARIABLE revision OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
  if(NOT revision)
    set(revision "uncommitted")
  endif()
  set(seed "git:${revision}\nboard:${BOARD}\nzephyr:${ZEPHYR_VERSION}\n")
  string(APPEND seed "compiler:${CMAKE_C_COMPILER_ID}:${CMAKE_C_COMPILER_VERSION}\n")
  set(source_index 0)
  foreach(source IN LISTS DIAG_SOURCES)
    if(NOT EXISTS "${source}")
      message(FATAL_ERROR "Diagnostic fingerprint source missing: ${source}")
    endif()
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${source}")
    file(SHA256 "${source}" digest)
    # Deliberately omit absolute checkout paths so identical ordered inputs in
    # two workspace locations produce the same mismatch guard.
    string(APPEND seed "source-${source_index}:${digest}\n")
    math(EXPR source_index "${source_index} + 1")
  endforeach()
  if(EXISTS "${APPLICATION_BINARY_DIR}/zephyr/.config")
    file(SHA256 "${APPLICATION_BINARY_DIR}/zephyr/.config" config_digest)
    string(APPEND seed "config:${config_digest}\n")
  endif()
  execute_process(COMMAND git symbolic-ref -q HEAD
    WORKING_DIRECTORY "${APPLICATION_SOURCE_DIR}"
    OUTPUT_VARIABLE git_ref OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
  foreach(item IN ITEMS HEAD "${git_ref}")
    if(item)
      execute_process(COMMAND git rev-parse --git-path "${item}"
        WORKING_DIRECTORY "${APPLICATION_SOURCE_DIR}"
        OUTPUT_VARIABLE path OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
      if(path)
        get_filename_component(path "${path}" ABSOLUTE BASE_DIR "${APPLICATION_SOURCE_DIR}")
        if(EXISTS "${path}")
          set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${path}")
        endif()
      endif()
    endif()
  endforeach()
  string(SHA256 digest "${seed}")
  string(SUBSTRING "${digest}" 0 20 digest)
  set(${output_variable} "${digest}" PARENT_SCOPE)
endfunction()
