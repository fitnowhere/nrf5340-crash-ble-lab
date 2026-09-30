zephyr_library()
zephyr_library_sources_ifdef(CONFIG_DIAG_SDK src/diag_sdk.c)
zephyr_include_directories(include)
