/*
    CrowPanel Advanced 7inch(ESP32-P4)用 AI会話ビューア。

    タッチ操作で参加AI・モデレーター・トーン・ターン数・テーマ(固定候補、または英語での
    自由入力)を選び、開始でRaspberry Piに会話開始を指示する。開始後は会話ログ画面に
    切り替わり、一時停止/再開/停止を操作できる。
    日本語フォント(常用漢字+かな、main/fonts/notosans_jp_20.c)を使用している。

    会話ログ画面の表示は、Pi版Web UI(1.Raspberry Pi版/templates/index.html)と同じ雰囲気に
    そろえてある。発言(message)はLINE風の色分け吹き出し(Manus=左/緑, GPT=右/青,
    Claude=中央/橙, GLM=左/紫)、要約(summary)は吹き出しにせず画面横幅いっぱいの
    パネルで表示する。

    Wi-Fi・PiのURLは、ソースに直接書かず `idf.py menuconfig` の
    「AI Conversation (CrowPanel) Settings」で設定する(main/Kconfig.projbuild)。
*/
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <esp_wifi.h>
#include <esp_log.h>
#include <esp_err.h>
#include <nvs_flash.h>
#include <esp_timer.h>

#include "bsp_display.h"
#include "bsp_wifi.h"
#include "conversation_client.h"

/* 常用漢字2,136字+かな+記号を収録した日本語フォント(NotoSansJP-Bold, 20px, 4bpp)。
 * main/fonts/notosans_jp_20.c 参照(再生成の手順は 2.ESP32版/README.md)。
 *
 * 収録字数を増やす(例: JIS第一+第二水準の約6,400字。字リストは
 * main/fonts/kanji_symbols_jis_level1_2.txt)と、Flash上のフォントデータが増えた分だけ
 * 描画時のFlash読み出しが増える。試作では、画面周囲が白くちらつく症状が出た
 * (2,136字に戻すと解消)。原因は調査中(ESP32-P4ではFlashとPSRAMがL2キャッシュを
 * 共有しており、その競合を疑っている)。そのため現在は常用漢字2,136字に据え置いている。 */
LV_FONT_DECLARE(notosans_jp_20);

#ifdef HAVE_SPLASH_LOGO
/* 起動時に数秒間表示するスプラッシュ画像(任意)。
 * main/splash_logo.bin(1024x600のRGB565生データ。tools/make_splash_bin.pyで作成)が
 * 存在する場合だけ、main/CMakeLists.txtがEMBED_FILESでバイナリのままFlashに埋め込み、
 * HAVE_SPLASH_LOGOを定義する。objcopyが生成するシンボル経由で参照するため、
 * C配列のソースを経由せず、ビルドも軽い。 */
extern const uint8_t splash_logo_bin_start[] asm("_binary_splash_logo_bin_start");
extern const uint8_t splash_logo_bin_end[]   asm("_binary_splash_logo_bin_end");

#define SPLASH_LOGO_W 1024
#define SPLASH_LOGO_H 600
#define SPLASH_DURATION_MS 3000

static const lv_img_dsc_t splash_logo_dsc = {
    .header.cf = LV_IMG_CF_TRUE_COLOR,
    .header.always_zero = 0,
    .header.reserved = 0,
    .header.w = SPLASH_LOGO_W,
    .header.h = SPLASH_LOGO_H,
    .data_size = SPLASH_LOGO_W * SPLASH_LOGO_H * 2,
    .data = splash_logo_bin_start,
};
#endif /* HAVE_SPLASH_LOGO */

#define TAG "MAIN"
#define MAIN_INFO(fmt, ...) ESP_LOGI(TAG, fmt, ##__VA_ARGS__)
#define MAIN_ERROR(fmt, ...) ESP_LOGE(TAG, fmt, ##__VA_ARGS__)

#define init_fail(fmt, ...) ESP_LOGE(TAG, fmt":%d", ##__VA_ARGS__)

/* Wi-Fi(2.4GHz)とRaspberry Pi側Flaskサーバーの情報は、menuconfigで設定する
 * (main/Kconfig.projbuild)。PiのIPアドレスは、Pi上で `hostname -I` を実行すると確認できる。
 * 値はビルド生成物(sdkconfig)にのみ入り、ソースには書かれない(sdkconfigは.gitignore済み)。 */
#define WIFI_SSID     CONFIG_CONVERSATION_WIFI_SSID
#define WIFI_PASSWORD CONFIG_CONVERSATION_WIFI_PASSWORD
#define PI_BASE_URL   CONFIG_CONVERSATION_PI_BASE_URL

#define POLL_INTERVAL_MS 2000
/* これを超えたら古い行(発言の吹き出し・要約パネル等、log_containerの直接の子1個単位)から
 * 削除する。日本語(カスタムCJKビットマップフォント)の長文ラベルが多数溜まった状態で
 * スクロールすると再描画が重くなり、タスクウォッチドッグに引っかかったことがあるため、
 * 当初の60から削減している。吹き出し・要約パネル化でlv_obj_t数が
 * 1件あたり大幅に増えたため(LVGL側の固定メモリプールを使い切ってクラッシュしたことがある)、
 * さらに20→12へ削減し、内部RAM側の専用プール(128KB)に余裕を持たせている。 */
#define LOG_MAX_LINES 12

/* Pi側 conversation_engine.py の CANONICAL_ORDER / PROVIDER_DISPLAY_NAME と同じ並び。 */
#define PROVIDER_COUNT 4
static const char *PROVIDER_IDS[PROVIDER_COUNT]    = {"manus", "openai", "claude", "zai"};
static const char *PROVIDER_LABELS[PROVIDER_COUNT] = {"Manus", "GPT",    "Claude", "GLM"};

/* 発言者ごとの吹き出しスタイル。Pi版Web UI(templates/index.html)の配色・
 * 寄せ位置(Manus=左, GPT=右, Claude=中央, GLM=左)とそろえ、同じ「見た目の雰囲気」にする。 */
typedef struct {
    uint32_t bubble_bg;
    uint32_t bubble_border;
    uint32_t bubble_text;
    lv_align_t align;
} provider_style_t;

static const provider_style_t PROVIDER_STYLES[PROVIDER_COUNT] = {
    /* Manus  */ { 0xE8F8EE, 0x16A34A, 0x14532D, LV_ALIGN_TOP_LEFT },
    /* GPT    */ { 0xEAF1FF, 0x2563EB, 0x1E3A8A, LV_ALIGN_TOP_RIGHT },
    /* Claude */ { 0xFDF0E6, 0xC2410C, 0x7C2D12, LV_ALIGN_TOP_MID },
    /* GLM    */ { 0xF3E8FF, 0x7C3AED, 0x4C1D95, LV_ALIGN_TOP_LEFT },
};

/* 吹き出し本文の最大幅(px)。これを超える長さの発言はこの幅で折り返す。 */
#define BUBBLE_MAX_WIDTH_PX 720

static const provider_style_t *style_for_speaker(const char *speaker)
{
    if (speaker == NULL) {
        return NULL;
    }
    for (int i = 0; i < PROVIDER_COUNT; i++) {
        if (strcmp(speaker, PROVIDER_LABELS[i]) == 0) {
            return &PROVIDER_STYLES[i];
        }
    }
    return NULL;
}

/* Pi側 conversation_engine.py の TONE_PRESETS と同じ(キー, 表示名)。 */
#define TONE_COUNT 2
static const char *TONE_KEYS[TONE_COUNT]   = {"1", "2"};
static const char *TONE_OPTIONS = "真面目モード\nフレンドリーモード";

static const char *TURNS_OPTIONS = "2\n4\n6\n8\n10\n12\n15\n20";

/* 固定候補のテーマ(自由入力を使わない場合の候補リスト)。 */
static const char *TOPIC_OPTIONS =
    "最近美味しいと感じたもの\n"
    "新商品のアイデアを出し合う\n"
    "AIが仕事に与える影響\n"
    "理想の休日の過ごし方";

#define MIN_PARTICIPANTS 2
#define MAX_PARTICIPANTS 4

/* ===== UIウィジェット(状態) ===== */
static lv_obj_t *setup_screen;
static lv_obj_t *conversation_screen;

static lv_obj_t *ai_checkboxes[PROVIDER_COUNT];
static lv_obj_t *moderator_dropdown;
static lv_obj_t *tone_dropdown;
static lv_obj_t *turns_dropdown;
static lv_obj_t *topic_dropdown;
static lv_obj_t *topic_textarea;
static lv_obj_t *keyboard;
static lv_obj_t *start_btn;
static lv_obj_t *status_label;

static lv_obj_t *log_container;
static lv_obj_t *control_btn_label;
static lv_obj_t *stop_btn_label;

/* モデレータードロップダウンの選択肢(表示中)が、どのプロバイダIDに対応するかの対応表。
 * refresh_moderator_options() を呼ぶたびに作り直す。 */
static const char *moderator_option_providers[PROVIDER_COUNT];
static int moderator_option_count = 0;

static bool s_is_paused = false;
static bool s_conversation_ended = false;

static void show_setup_screen(void);
static void show_conversation_screen(void);

/* ===== 会話ログ表示 ===== */

static void format_event_line(const cJSON *event, char *out, size_t out_size)
{
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(event, "type");
    const char *type_str = cJSON_IsString(type) ? type->valuestring : "";

    if (strcmp(type_str, "session_start") == 0) {
        const cJSON *topic = cJSON_GetObjectItemCaseSensitive(event, "topic");
        const cJSON *moderator = cJSON_GetObjectItemCaseSensitive(event, "moderator");
        snprintf(out, out_size, "=== %s (%s) ===",
                 cJSON_IsString(topic) ? topic->valuestring : "?",
                 cJSON_IsString(moderator) ? moderator->valuestring : "?");
    } else if (strcmp(type_str, "status") == 0) {
        const cJSON *agent = cJSON_GetObjectItemCaseSensitive(event, "agent");
        const cJSON *state = cJSON_GetObjectItemCaseSensitive(event, "state");
        snprintf(out, out_size, "[%s: %s...]",
                 cJSON_IsString(agent) ? agent->valuestring : "?",
                 cJSON_IsString(state) ? state->valuestring : "");
    } else if (strcmp(type_str, "message") == 0) {
        const cJSON *speaker = cJSON_GetObjectItemCaseSensitive(event, "speaker");
        const cJSON *text = cJSON_GetObjectItemCaseSensitive(event, "text");
        snprintf(out, out_size, "%s: %s",
                 cJSON_IsString(speaker) ? speaker->valuestring : "?",
                 cJSON_IsString(text) ? text->valuestring : "");
    } else if (strcmp(type_str, "summary") == 0) {
        const cJSON *overall = cJSON_GetObjectItemCaseSensitive(event, "overall_summary");
        snprintf(out, out_size, "--- 要約 ---\n%s",
                 cJSON_IsString(overall) ? overall->valuestring : "");
    } else if (strcmp(type_str, "error") == 0) {
        const cJSON *message = cJSON_GetObjectItemCaseSensitive(event, "message");
        snprintf(out, out_size, "[エラー] %s",
                 cJSON_IsString(message) ? message->valuestring : "?");
    } else if (strcmp(type_str, "session_end") == 0) {
        snprintf(out, out_size, "=== 終了 ===");
    } else {
        snprintf(out, out_size, "[%s]", type_str);
    }
}

/* 発言(message)をLINE風の吹き出しで表示する。
 * 行(row, 幅100%・透明・自由配置)の中に、発言者の色に応じて左/右/中央へ寄せた
 * 吹き出し(bubble)を置く構成。吹き出しの幅は本文の実測幅(lv_txt_get_size)を使い、
 * 短い発言は本文に合わせて縮み、長い発言はBUBBLE_MAX_WIDTH_PXで折り返す。 */
static void append_message_bubble(const cJSON *event)
{
    const cJSON *speaker = cJSON_GetObjectItemCaseSensitive(event, "speaker");
    const cJSON *text = cJSON_GetObjectItemCaseSensitive(event, "text");
    const cJSON *turn = cJSON_GetObjectItemCaseSensitive(event, "turn");
    const char *speaker_str = cJSON_IsString(speaker) ? speaker->valuestring : "?";
    const char *text_str = cJSON_IsString(text) ? text->valuestring : "";
    int turn_num = cJSON_IsNumber(turn) ? turn->valueint : 0;

    const provider_style_t *style = style_for_speaker(speaker_str);
    uint32_t bg = style ? style->bubble_bg : 0xEEEEEE;
    uint32_t border = style ? style->bubble_border : 0x888888;
    uint32_t text_color = style ? style->bubble_text : 0x222222;
    lv_align_t align = style ? style->align : LV_ALIGN_TOP_LEFT;

    lv_point_t natural_size;
    lv_txt_get_size(&natural_size, text_str, &notosans_jp_20, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    lv_coord_t label_w = natural_size.x < BUBBLE_MAX_WIDTH_PX ? natural_size.x : BUBBLE_MAX_WIDTH_PX;
    if (label_w < 20) {
        label_w = 20;
    }

    lv_obj_t *row = lv_obj_create(log_container);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_height(row, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row, 0, 0);
    lv_obj_set_style_pad_all(row, 0, 0);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *bubble = lv_obj_create(row);
    lv_obj_set_width(bubble, LV_SIZE_CONTENT);
    lv_obj_set_height(bubble, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_color(bubble, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_opa(bubble, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(bubble, lv_color_hex(border), 0);
    lv_obj_set_style_border_width(bubble, 1, 0);
    lv_obj_set_style_radius(bubble, 12, 0);
    lv_obj_set_style_pad_all(bubble, 10, 0);
    lv_obj_set_style_pad_row(bubble, 4, 0);
    lv_obj_set_flex_flow(bubble, LV_FLEX_FLOW_COLUMN);
    lv_obj_clear_flag(bubble, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_align(bubble, align, 0, 0);

    lv_obj_t *meta_label = lv_label_create(bubble);
    lv_label_set_text_fmt(meta_label, "Turn %d - %s", turn_num, speaker_str);
    lv_obj_set_style_text_color(meta_label, lv_color_hex(text_color), 0);
    lv_obj_set_style_text_opa(meta_label, LV_OPA_70, 0);

    lv_obj_t *body_label = lv_label_create(bubble);
    lv_label_set_long_mode(body_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(body_label, label_w);
    lv_label_set_text(body_label, text_str);
    lv_obj_set_style_text_color(body_label, lv_color_hex(text_color), 0);

    lv_obj_scroll_to_view(row, LV_ANIM_OFF);
}

/* 見出し付きの箱(要約パネルの各セクションで使い回す)。 */
static lv_obj_t *create_summary_box(lv_obj_t *parent, uint32_t bg, uint32_t border, uint32_t border_w)
{
    lv_obj_t *box = lv_obj_create(parent);
    lv_obj_set_width(box, lv_pct(100));
    lv_obj_set_height(box, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_color(box, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(box, lv_color_hex(border), 0);
    lv_obj_set_style_border_width(box, border_w, 0);
    lv_obj_set_style_radius(box, 8, 0);
    lv_obj_set_style_pad_all(box, 12, 0);
    lv_obj_set_style_pad_row(box, 4, 0);
    lv_obj_set_flex_flow(box, LV_FLEX_FLOW_COLUMN);
    lv_obj_clear_flag(box, LV_OBJ_FLAG_SCROLLABLE);
    return box;
}

static lv_obj_t *create_wrapped_label(lv_obj_t *parent, uint32_t color)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_width(label, lv_pct(100));
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    return label;
}

/* 要約(summary)はPi版と同様、吹き出しにはせず画面の横幅いっぱいに表示する。
 * overall_summary → per_speaker(発言者ごとの視点) → key_issues(論点) →
 * divergent_points(見解の相違) → raw_text(構造化失敗時のフォールバック) の順。 */
static void append_summary_panel(const cJSON *event)
{
    lv_obj_t *panel = lv_obj_create(log_container);
    lv_obj_set_width(panel, lv_pct(100));
    lv_obj_set_height(panel, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_opa(panel, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(panel, 0, 0);
    lv_obj_set_style_pad_all(panel, 0, 0);
    lv_obj_set_style_pad_row(panel, 10, 0);
    lv_obj_set_flex_flow(panel, LV_FLEX_FLOW_COLUMN);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(panel);
    lv_obj_set_width(title, lv_pct(100));
    lv_label_set_text(title, "--- 会話の要約 ---");
    lv_obj_set_style_text_color(title, lv_color_hex(0xE5E7EB), 0);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, 0);

    const cJSON *overall = cJSON_GetObjectItemCaseSensitive(event, "overall_summary");
    if (cJSON_IsString(overall) && overall->valuestring[0] != '\0') {
        lv_obj_t *box = create_summary_box(panel, 0x1E2530, 0x1E2530, 0);
        lv_obj_t *label = create_wrapped_label(box, 0xE5E7EB);
        lv_label_set_text(label, overall->valuestring);
    }

    const cJSON *per_speaker = cJSON_GetObjectItemCaseSensitive(event, "per_speaker");
    if (cJSON_IsArray(per_speaker)) {
        const cJSON *item;
        cJSON_ArrayForEach(item, per_speaker) {
            const cJSON *speaker = cJSON_GetObjectItemCaseSensitive(item, "speaker");
            const char *speaker_str = cJSON_IsString(speaker) ? speaker->valuestring : "?";
            const provider_style_t *style = style_for_speaker(speaker_str);
            uint32_t header_color = style ? style->bubble_border : 0xAAAAAA;

            lv_obj_t *wrap = lv_obj_create(panel);
            lv_obj_set_width(wrap, lv_pct(100));
            lv_obj_set_height(wrap, LV_SIZE_CONTENT);
            lv_obj_set_style_bg_opa(wrap, LV_OPA_TRANSP, 0);
            lv_obj_set_style_border_width(wrap, 0, 0);
            lv_obj_set_style_pad_all(wrap, 0, 0);
            lv_obj_set_style_pad_row(wrap, 2, 0);
            lv_obj_set_flex_flow(wrap, LV_FLEX_FLOW_COLUMN);
            lv_obj_clear_flag(wrap, LV_OBJ_FLAG_SCROLLABLE);

            lv_obj_t *header = lv_label_create(wrap);
            lv_label_set_text_fmt(header, "%s の視点", speaker_str);
            lv_obj_set_style_text_color(header, lv_color_hex(header_color), 0);

            const cJSON *points = cJSON_GetObjectItemCaseSensitive(item, "points");
            if (cJSON_IsArray(points)) {
                const cJSON *pt;
                cJSON_ArrayForEach(pt, points) {
                    if (!cJSON_IsString(pt)) {
                        continue;
                    }
                    lv_obj_t *li = create_wrapped_label(wrap, 0xE5E7EB);
                    lv_label_set_text_fmt(li, "・%s", pt->valuestring);
                }
            }
        }
    }

    const cJSON *key_issues = cJSON_GetObjectItemCaseSensitive(event, "key_issues");
    if (cJSON_IsArray(key_issues) && cJSON_GetArraySize(key_issues) > 0) {
        lv_obj_t *box = create_summary_box(panel, 0x3A2E12, 0xD97706, 1);
        lv_obj_t *header = lv_label_create(box);
        lv_label_set_text(header, "主な論点・課題");
        lv_obj_set_style_text_color(header, lv_color_hex(0xFDE68A), 0);

        const cJSON *k;
        cJSON_ArrayForEach(k, key_issues) {
            if (!cJSON_IsString(k)) {
                continue;
            }
            lv_obj_t *li = create_wrapped_label(box, 0xFDE68A);
            lv_label_set_text_fmt(li, "・%s", k->valuestring);
        }
    }

    const cJSON *divergent = cJSON_GetObjectItemCaseSensitive(event, "divergent_points");
    if (cJSON_IsArray(divergent) && cJSON_GetArraySize(divergent) > 0) {
        lv_obj_t *box = create_summary_box(panel, 0x3A1414, 0xDC2626, 1);
        lv_obj_t *header = lv_label_create(box);
        lv_label_set_text(header, "AI間で見解が分かれた点");
        lv_obj_set_style_text_color(header, lv_color_hex(0xFCA5A5), 0);

        const cJSON *d;
        cJSON_ArrayForEach(d, divergent) {
            const cJSON *point = cJSON_GetObjectItemCaseSensitive(d, "point");
            const cJSON *desc = cJSON_GetObjectItemCaseSensitive(d, "description");
            lv_obj_t *li = create_wrapped_label(box, 0xFCA5A5);
            if (cJSON_IsString(desc) && desc->valuestring[0] != '\0') {
                lv_label_set_text_fmt(li, "・%s - %s",
                                       cJSON_IsString(point) ? point->valuestring : "",
                                       desc->valuestring);
            } else {
                lv_label_set_text_fmt(li, "・%s", cJSON_IsString(point) ? point->valuestring : "");
            }
        }
    }

    const cJSON *raw_text = cJSON_GetObjectItemCaseSensitive(event, "raw_text");
    if (cJSON_IsString(raw_text) && raw_text->valuestring[0] != '\0') {
        lv_obj_t *note = create_wrapped_label(panel, 0x9CA3AF);
        lv_label_set_text(note, "(構造化に失敗したため、要約AIの応答をそのまま表示しています)");

        lv_obj_t *raw_label = create_wrapped_label(panel, 0xE5E7EB);
        lv_label_set_text(raw_label, raw_text->valuestring);
    }

    lv_obj_scroll_to_view(panel, LV_ANIM_OFF);
}

/* session_start/status/error/session_end等、吹き出し化しないイベントは
 * 中央寄せの地の文として表示する(従来通り)。 */
static void append_plain_line(const char *line, const char *type_str)
{
    lv_obj_t *label = lv_label_create(log_container);
    lv_obj_set_width(label, lv_pct(100));
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    lv_label_set_text(label, line);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);

    uint32_t color = 0xAAAAAA;
    if (strcmp(type_str, "error") == 0) {
        color = 0xFF6B6B;
    }
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);

    lv_obj_scroll_to_view(label, LV_ANIM_OFF);
}

static void on_conversation_event(const cJSON *event, void *ctx)
{
    (void)ctx;
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(event, "type");
    const char *type_str = cJSON_IsString(type) ? type->valuestring : "";
    if (strcmp(type_str, "session_end") == 0) {
        s_conversation_ended = true;
    }

    char line[512];
    format_event_line(event, line, sizeof(line));
    MAIN_INFO("%s", line);

    if (lvgl_port_lock(0)) {
        while (lv_obj_get_child_cnt(log_container) >= LOG_MAX_LINES) {
            lv_obj_del(lv_obj_get_child(log_container, 0));
        }

        if (strcmp(type_str, "message") == 0) {
            append_message_bubble(event);
        } else if (strcmp(type_str, "summary") == 0) {
            append_summary_panel(event);
        } else {
            append_plain_line(line, type_str);
        }

        if (s_conversation_ended && stop_btn_label != NULL) {
            lv_label_set_text(stop_btn_label, "戻る");
        }

        lvgl_port_unlock();
    }
}

/* ===== 設定画面 ===== */

static void refresh_moderator_options(void)
{
    char options[256] = "";
    moderator_option_count = 0;

    for (int i = 0; i < PROVIDER_COUNT; i++) {
        if (lv_obj_has_state(ai_checkboxes[i], LV_STATE_CHECKED)) {
            if (moderator_option_count > 0) {
                strncat(options, "\n", sizeof(options) - strlen(options) - 1);
            }
            strncat(options, PROVIDER_LABELS[i], sizeof(options) - strlen(options) - 1);
            moderator_option_providers[moderator_option_count] = PROVIDER_IDS[i];
            moderator_option_count++;
        }
    }

    if (moderator_option_count == 0) {
        strncpy(options, "-", sizeof(options) - 1);
    }

    lv_dropdown_set_options(moderator_dropdown, options);
}

static void ai_checkbox_event_cb(lv_event_t *e)
{
    (void)e;
    refresh_moderator_options();
}

/* 自由入力のテーマ(英語)。lv_keyboardは英数字のみ対応で日本語入力(IME)は
 * サポートしていないため、日本語のテーマは引き続き固定候補リストから選ぶ。
 * このテキストエリアに入力があれば、そちらを優先してテーマとして使う。 */
static void keyboard_event_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_READY || code == LV_EVENT_CANCEL) {
        lv_obj_add_flag(keyboard, LV_OBJ_FLAG_HIDDEN);
    }
}

static void topic_textarea_event_cb(lv_event_t *e)
{
    (void)e;
    lv_keyboard_set_textarea(keyboard, topic_textarea);
    lv_obj_clear_flag(keyboard, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(keyboard);
}

static void start_btn_event_cb(lv_event_t *e)
{
    (void)e;

    lv_obj_add_flag(keyboard, LV_OBJ_FLAG_HIDDEN);

    const char *selected_providers[PROVIDER_COUNT];
    int selected_count = 0;
    for (int i = 0; i < PROVIDER_COUNT; i++) {
        if (lv_obj_has_state(ai_checkboxes[i], LV_STATE_CHECKED)) {
            selected_providers[selected_count++] = PROVIDER_IDS[i];
        }
    }

    if (selected_count < MIN_PARTICIPANTS || selected_count > MAX_PARTICIPANTS) {
        lv_label_set_text_fmt(status_label, "AIを%d〜%d体選んでください", MIN_PARTICIPANTS, MAX_PARTICIPANTS);
        return;
    }
    if (moderator_option_count == 0) {
        lv_label_set_text(status_label, "モデレーター候補がいません");
        return;
    }

    uint16_t moderator_idx = lv_dropdown_get_selected(moderator_dropdown);
    if (moderator_idx >= moderator_option_count) {
        moderator_idx = 0;
    }
    const char *moderator = moderator_option_providers[moderator_idx];

    uint16_t tone_idx = lv_dropdown_get_selected(tone_dropdown);
    const char *tone_key = TONE_KEYS[tone_idx < TONE_COUNT ? tone_idx : 0];

    char turns_str[8];
    lv_dropdown_get_selected_str(turns_dropdown, turns_str, sizeof(turns_str));
    int max_turns = atoi(turns_str);
    if (max_turns <= 0) {
        max_turns = 6;
    }

    char topic[128];
    const char *custom_topic = lv_textarea_get_text(topic_textarea);
    if (custom_topic != NULL && custom_topic[0] != '\0') {
        strncpy(topic, custom_topic, sizeof(topic) - 1);
        topic[sizeof(topic) - 1] = '\0';
    } else {
        lv_dropdown_get_selected_str(topic_dropdown, topic, sizeof(topic));
    }

    lv_label_set_text(status_label, "開始しています...");

    char err[96];
    bool ok = conversation_client_start(topic, tone_key, max_turns,
                                         selected_providers, selected_count,
                                         moderator, err, sizeof(err));
    if (!ok) {
        lv_label_set_text_fmt(status_label, "失敗: %s", err);
        return;
    }

    lv_label_set_text(status_label, "");
    show_conversation_screen();
}

static void build_setup_screen(lv_obj_t *scr)
{
    setup_screen = lv_obj_create(scr);
    lv_obj_set_size(setup_screen, LV_HOR_RES, LV_VER_RES);
    lv_obj_align(setup_screen, LV_ALIGN_TOP_LEFT, 0, 0);
    lv_obj_set_style_bg_color(setup_screen, lv_color_hex(0x141821), 0);
    lv_obj_set_style_bg_opa(setup_screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(setup_screen, 0, 0);
    lv_obj_set_style_pad_all(setup_screen, 20, 0);
    lv_obj_set_style_pad_row(setup_screen, 14, 0);
    lv_obj_set_flex_flow(setup_screen, LV_FLEX_FLOW_COLUMN);

    /* 参加AI選択 */
    lv_obj_t *ai_row = lv_obj_create(setup_screen);
    lv_obj_set_size(ai_row, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_style_bg_opa(ai_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(ai_row, 0, 0);
    lv_obj_set_style_pad_all(ai_row, 0, 0);
    lv_obj_set_style_pad_top(ai_row, 8, 0);
    lv_obj_set_style_pad_bottom(ai_row, 8, 0);
    lv_obj_set_style_pad_column(ai_row, 24, 0);
    lv_obj_set_flex_flow(ai_row, LV_FLEX_FLOW_ROW);
    lv_obj_clear_flag(ai_row, LV_OBJ_FLAG_SCROLLABLE);

    for (int i = 0; i < PROVIDER_COUNT; i++) {
        ai_checkboxes[i] = lv_checkbox_create(ai_row);
        lv_checkbox_set_text(ai_checkboxes[i], PROVIDER_LABELS[i]);
        lv_obj_set_style_text_color(ai_checkboxes[i], lv_color_hex(0xFFFFFF), 0);
        lv_obj_set_style_pad_all(ai_checkboxes[i], 6, 0);
        /* 見た目の大きさは変えず、タップ判定域だけ周囲12pxぶん広げる(角にあるManusが
         * 特に反応しにくいという指摘への対応)。 */
        lv_obj_set_ext_click_area(ai_checkboxes[i], 12);
        lv_obj_add_event_cb(ai_checkboxes[i], ai_checkbox_event_cb, LV_EVENT_VALUE_CHANGED, NULL);
    }
    /* 既定でManus/GPTの2体を選択しておく */
    lv_obj_add_state(ai_checkboxes[0], LV_STATE_CHECKED);
    lv_obj_add_state(ai_checkboxes[1], LV_STATE_CHECKED);

    /* モデレーター */
    lv_obj_t *mod_label = lv_label_create(setup_screen);
    lv_label_set_text(mod_label, "モデレーター");
    lv_obj_set_style_text_color(mod_label, lv_color_hex(0xAAAAAA), 0);

    moderator_dropdown = lv_dropdown_create(setup_screen);
    lv_obj_set_width(moderator_dropdown, 300);
    refresh_moderator_options();

    /* トーン */
    lv_obj_t *tone_label = lv_label_create(setup_screen);
    lv_label_set_text(tone_label, "トーン");
    lv_obj_set_style_text_color(tone_label, lv_color_hex(0xAAAAAA), 0);

    tone_dropdown = lv_dropdown_create(setup_screen);
    lv_obj_set_width(tone_dropdown, 300);
    lv_dropdown_set_options(tone_dropdown, TONE_OPTIONS);

    /* ターン数 */
    lv_obj_t *turns_label = lv_label_create(setup_screen);
    lv_label_set_text(turns_label, "ターン数");
    lv_obj_set_style_text_color(turns_label, lv_color_hex(0xAAAAAA), 0);

    turns_dropdown = lv_dropdown_create(setup_screen);
    lv_obj_set_width(turns_dropdown, 150);
    lv_dropdown_set_options(turns_dropdown, TURNS_OPTIONS);
    lv_dropdown_set_selected(turns_dropdown, 2); /* "6" */

    /* テーマ(固定候補) */
    lv_obj_t *topic_label = lv_label_create(setup_screen);
    lv_label_set_text(topic_label, "テーマ");
    lv_obj_set_style_text_color(topic_label, lv_color_hex(0xAAAAAA), 0);

    topic_dropdown = lv_dropdown_create(setup_screen);
    lv_obj_set_width(topic_dropdown, lv_pct(90));
    lv_dropdown_set_options(topic_dropdown, TOPIC_OPTIONS);

    /* テーマ(自由入力・英語のみ)。入力があればこちらを優先する。 */
    lv_obj_t *custom_topic_label = lv_label_create(setup_screen);
    lv_label_set_text(custom_topic_label, "または自由入力(英語のみ)");
    lv_obj_set_style_text_color(custom_topic_label, lv_color_hex(0xAAAAAA), 0);

    topic_textarea = lv_textarea_create(setup_screen);
    lv_obj_set_size(topic_textarea, lv_pct(90), 50);
    lv_textarea_set_one_line(topic_textarea, true);
    lv_textarea_set_max_length(topic_textarea, 100);
    lv_textarea_set_placeholder_text(topic_textarea, "(任意)");
    lv_obj_add_event_cb(topic_textarea, topic_textarea_event_cb, LV_EVENT_CLICKED, NULL);

    /* START */
    start_btn = lv_btn_create(setup_screen);
    lv_obj_set_size(start_btn, 200, 60);
    lv_obj_add_event_cb(start_btn, start_btn_event_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *start_btn_label = lv_label_create(start_btn);
    lv_label_set_text(start_btn_label, "開始");
    lv_obj_center(start_btn_label);

    status_label = lv_label_create(setup_screen);
    lv_obj_set_width(status_label, lv_pct(100));
    lv_label_set_long_mode(status_label, LV_LABEL_LONG_WRAP);
    lv_label_set_text(status_label, "Wi-Fi接続中...");
    lv_obj_set_style_text_color(status_label, lv_color_hex(0xFF8888), 0);

    lv_obj_add_flag(start_btn, LV_OBJ_FLAG_HIDDEN);

    /* オンスクリーンキーボード(英数字のみ)。topic_textareaがフォーカスされた時だけ表示する。 */
    keyboard = lv_keyboard_create(setup_screen);
    lv_obj_set_size(keyboard, LV_HOR_RES, LV_VER_RES / 2);
    lv_obj_align(keyboard, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_add_event_cb(keyboard, keyboard_event_cb, LV_EVENT_ALL, NULL);
    lv_obj_add_flag(keyboard, LV_OBJ_FLAG_HIDDEN);
}

/* ===== 会話画面 ===== */

static void control_btn_event_cb(lv_event_t *e)
{
    (void)e;
    char err[96];
    if (s_conversation_ended) {
        /* 会話は既に終わっているので、単に設定画面へ戻るだけ */
        show_setup_screen();
        return;
    }

    if (!s_is_paused) {
        if (conversation_client_control("pause", err, sizeof(err))) {
            s_is_paused = true;
            lv_label_set_text(control_btn_label, "再開");
        }
    } else {
        if (conversation_client_control("resume", err, sizeof(err))) {
            s_is_paused = false;
            lv_label_set_text(control_btn_label, "一時停止");
        }
    }
}

static void stop_btn_event_cb(lv_event_t *e)
{
    (void)e;
    char err[96];
    if (!s_conversation_ended) {
        conversation_client_control("stop", err, sizeof(err));
    }
    show_setup_screen();
}

static void build_conversation_screen(lv_obj_t *scr)
{
    conversation_screen = lv_obj_create(scr);
    lv_obj_set_size(conversation_screen, LV_HOR_RES, LV_VER_RES);
    lv_obj_align(conversation_screen, LV_ALIGN_TOP_LEFT, 0, 0);
    lv_obj_set_style_bg_color(conversation_screen, lv_color_hex(0x141821), 0);
    lv_obj_set_style_bg_opa(conversation_screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(conversation_screen, 0, 0);
    lv_obj_set_style_pad_all(conversation_screen, 0, 0);
    lv_obj_set_flex_flow(conversation_screen, LV_FLEX_FLOW_COLUMN);

    log_container = lv_obj_create(conversation_screen);
    lv_obj_set_width(log_container, lv_pct(100));
    lv_obj_set_flex_grow(log_container, 1);
    lv_obj_set_style_bg_opa(log_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(log_container, 0, 0);
    lv_obj_set_style_pad_all(log_container, 16, 0);
    lv_obj_set_style_pad_row(log_container, 8, 0);
    lv_obj_set_flex_flow(log_container, LV_FLEX_FLOW_COLUMN);

    lv_obj_t *control_bar = lv_obj_create(conversation_screen);
    lv_obj_set_size(control_bar, lv_pct(100), 80);
    lv_obj_set_style_bg_opa(control_bar, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(control_bar, 0, 0);
    lv_obj_clear_flag(control_bar, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(control_bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(control_bar, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    lv_obj_t *control_btn = lv_btn_create(control_bar);
    lv_obj_set_size(control_btn, 180, 56);
    lv_obj_add_event_cb(control_btn, control_btn_event_cb, LV_EVENT_CLICKED, NULL);
    control_btn_label = lv_label_create(control_btn);
    lv_label_set_text(control_btn_label, "一時停止");
    lv_obj_center(control_btn_label);

    lv_obj_t *stop_btn = lv_btn_create(control_bar);
    lv_obj_set_size(stop_btn, 180, 56);
    lv_obj_set_style_bg_color(stop_btn, lv_color_hex(0x992222), 0);
    lv_obj_add_event_cb(stop_btn, stop_btn_event_cb, LV_EVENT_CLICKED, NULL);
    stop_btn_label = lv_label_create(stop_btn);
    lv_label_set_text(stop_btn_label, "停止");
    lv_obj_center(stop_btn_label);

    lv_obj_add_flag(conversation_screen, LV_OBJ_FLAG_HIDDEN);
}

#ifdef HAVE_SPLASH_LOGO
/* 起動直後、まだ設定画面を作る前の画面(display_init()直後は何もウィジェットが
 * 無く、バックライトも消灯中)に、スプラッシュ画像をduration_msだけ表示する。
 * 表示後は自分で作ったlv_imgオブジェクトだけを消し(scr自体は残す)、
 * この後build_setup_screen()/build_conversation_screen()が同じscr上にUIを組み立てる。 */
static void show_splash_screen(uint32_t duration_ms)
{
    if (lvgl_port_lock(0)) {
        lv_obj_t *scr = lv_scr_act();
        lv_obj_t *splash_img = lv_img_create(scr);
        lv_img_set_src(splash_img, &splash_logo_dsc);
        lv_obj_align(splash_img, LV_ALIGN_TOP_LEFT, 0, 0);
        lvgl_port_unlock();
    }

    set_lcd_blight(100);
    vTaskDelay(pdMS_TO_TICKS(duration_ms));

    if (lvgl_port_lock(0)) {
        lv_obj_clean(lv_scr_act());
        lvgl_port_unlock();
    }
}
#endif /* HAVE_SPLASH_LOGO */

/* ===== 画面切り替え ===== */

static void show_setup_screen(void)
{
    if (lvgl_port_lock(0)) {
        lv_textarea_set_text(topic_textarea, "");
        lv_obj_add_flag(keyboard, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(conversation_screen, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(setup_screen, LV_OBJ_FLAG_HIDDEN);
        lvgl_port_unlock();
    }
}

static void show_conversation_screen(void)
{
    if (lvgl_port_lock(0)) {
        lv_obj_clean(log_container);
        s_is_paused = false;
        s_conversation_ended = false;
        lv_label_set_text(control_btn_label, "一時停止");
        lv_label_set_text(stop_btn_label, "停止");

        lv_obj_add_flag(setup_screen, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(conversation_screen, LV_OBJ_FLAG_HIDDEN);
        lvgl_port_unlock();
    }
}

void app_main(void)
{
    /* Lesson16オリジナルのコメントでは「カメラ用」とされているが、bsp_display側には
     * 独自の電源投入処理がなく、この呼び出しに依存している(このLDOチャンネルが未投入だと
     * display_init()内のMIPI-DSIパネル初期化がハングし、タスクウォッチドッグで再起動を
     * 繰り返す)。カメラを使わない場合でも必ず呼び出すこと。 */
    static esp_ldo_channel_handle_t ldo3 = NULL;
    esp_ldo_channel_config_t ldo3_cof = {
        .chan_id = 3,
        .voltage_mv = 2500,
    };
    esp_err_t ldo_err = esp_ldo_acquire_channel(&ldo3_cof, &ldo3);
    if (ldo_err != ESP_OK)
        init_fail("ldo3", ldo_err);

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    esp_err_t err = i2c_init();
    if (err != ESP_OK)
        init_fail("i2c", err);

    err = touch_init();
    if (err != ESP_OK)
        init_fail("display touch", err);

    err = display_init();
    if (err != ESP_OK)
        init_fail("display", err);

#ifdef HAVE_SPLASH_LOGO
    show_splash_screen(SPLASH_DURATION_MS);
#endif

    bsp_wifi_init();
    bsp_wifi_sta_init();
    bsp_wifi_connect(WIFI_SSID, WIFI_PASSWORD);

    conversation_client_init(PI_BASE_URL);

    if (lvgl_port_lock(0)) {
        lv_obj_t *scr = lv_scr_act();
        /* text_fontはstyleの継承プロパティなので、ここで一度設定しておけば
         * 子(ラベル・チェックボックス・ドロップダウン等)は個別設定不要で日本語表示になる。 */
        lv_obj_set_style_text_font(scr, &notosans_jp_20, 0);
        build_setup_screen(scr);
        build_conversation_screen(scr);
        lvgl_port_unlock();
    }

    set_lcd_blight(100);

    while (WIFI_CONNECTED != bsp_wifi_get_state()) {
        MAIN_INFO("WIFI connecting......");
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    if (lvgl_port_lock(0)) {
        lv_label_set_text(status_label, "");
        lv_obj_clear_flag(start_btn, LV_OBJ_FLAG_HIDDEN);
        lvgl_port_unlock();
    }

    while (1) {
        conversation_client_poll(on_conversation_event, NULL);
        vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
    }
}
