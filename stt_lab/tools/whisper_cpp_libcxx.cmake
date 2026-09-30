# Build-only compatibility for LLVM libc++ 23. The pinned common.cpp uses
# std::partial_sort without directly including <algorithm>. Keep upstream source
# unchanged and provide the required standard header for that utility target.
get_property(stt_libcxx_fix_scheduled GLOBAL PROPERTY STT_WHISPER_CPP_LIBCXX_FIX_SCHEDULED)
if(NOT stt_libcxx_fix_scheduled)
    set_property(GLOBAL PROPERTY STT_WHISPER_CPP_LIBCXX_FIX_SCHEDULED TRUE)
    function(stt_whisper_cpp_libcxx_fix)
        if(TARGET common)
            target_compile_options(common PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:-include;algorithm>")
        endif()
        if(TARGET ggml-vulkan)
            # Clang 23 -O3 optimization of this host dispatch translation unit
            # exceeded nine CPU minutes. Keep kernels/source bytes unchanged;
            # scope -O1 to this one C++ file for the measured experimental build.
            set_source_files_properties(
                "${CMAKE_SOURCE_DIR}/ggml/src/ggml-vulkan/ggml-vulkan.cpp"
                TARGET_DIRECTORY ggml-vulkan PROPERTIES COMPILE_OPTIONS "-O1")
        endif()
    endfunction()
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL stt_whisper_cpp_libcxx_fix)
endif()
