# スプラッシュ画像(任意)。main/splash_logo.bin が存在する場合だけFlashに埋め込み、
# main.c 側の HAVE_SPLASH_LOGO を有効にする。作り方は 2.ESP32版/README.md 参照。
set(embed_files "")
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/splash_logo.bin")
    list(APPEND embed_files "splash_logo.bin")
endif()

idf_component_register(SRCS "main.c" "fonts/notosans_jp_20.c"
                    REQUIRES nvs_flash esp_wifi
                             bsp_i2c bsp_display bsp_wifi app_conversation
                    INCLUDE_DIRS "."
                    EMBED_FILES ${embed_files})

if(embed_files)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HAVE_SPLASH_LOGO=1)
endif()
