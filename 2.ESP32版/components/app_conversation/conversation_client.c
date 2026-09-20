#include "conversation_client.h"

#include <stdio.h>
#include <string.h>
#include <esp_http_client.h>
#include <esp_log.h>
#include <esp_heap_caps.h>

#define TAG "ConvClient"

/* 日本語1文字はJSON中で"\uXXXX"(6バイト)にエスケープされるため、実際の会話文よりも
 * かなり大きくなる。1回のポーリングで長い会話履歴(複数ターン+要約)を一括取得しても
 * 切れないよう、内部RAMを圧迫しないPSRAM上に大きめのバッファを確保する。 */
#define HTTP_RESPONSE_BUF_SIZE (64 * 1024)

static char s_base_url[96];
static char *s_http_response_buf = NULL;
static int s_http_response_len = 0;
static int s_since_seq = 0;

void conversation_client_init(const char *base_url)
{
    strncpy(s_base_url, base_url, sizeof(s_base_url) - 1);
    s_base_url[sizeof(s_base_url) - 1] = '\0';
    s_since_seq = 0;

    if (s_http_response_buf == NULL) {
        s_http_response_buf = heap_caps_malloc(HTTP_RESPONSE_BUF_SIZE, MALLOC_CAP_SPIRAM);
        if (s_http_response_buf == NULL) {
            ESP_LOGE(TAG, "Failed to allocate %d bytes from PSRAM for response buffer", HTTP_RESPONSE_BUF_SIZE);
        }
    }
}

/* Stage 3(station_example_main.c)と同じ蓄積パターン。
 * Flaskのjsonify()はContent-Lengthを返すので非chunked前提。 */
static esp_err_t http_event_handler(esp_http_client_event_t *evt)
{
    switch (evt->event_id) {
        case HTTP_EVENT_ON_DATA:
            if (s_http_response_buf != NULL && !esp_http_client_is_chunked_response(evt->client)) {
                int copy_len = evt->data_len;
                int remaining = HTTP_RESPONSE_BUF_SIZE - 1 - s_http_response_len;
                if (copy_len > remaining) {
                    ESP_LOGW(TAG, "Response buffer full, response truncated");
                    copy_len = remaining;
                }
                if (copy_len > 0) {
                    memcpy(s_http_response_buf + s_http_response_len, evt->data, copy_len);
                    s_http_response_len += copy_len;
                }
            }
            break;
        default:
            break;
    }
    return ESP_OK;
}

/* Pi側のconversation_engine.pyのDEFAULT_MODELと同じ値(高速・低コスト側)。
 * Pi側の定義を変えた場合はこちらも合わせて変更すること。 */
static const char *default_model_for(const char *provider)
{
    if (strcmp(provider, "openai") == 0) return "gpt-5.6-luna";
    if (strcmp(provider, "claude") == 0) return "claude-haiku-4-5-20251001";
    if (strcmp(provider, "zai") == 0) return "glm-5.3-flash";
    return NULL; /* manus: モデル選択非対応 */
}

/* path("/api/xxx")にJSON文字列をPOSTする共通処理。
 * 成功の判定はHTTPステータスがexpect_statusと一致するかどうかで行う。 */
static bool http_post_json(const char *path, const char *body, int expect_status,
                            char *err_out, size_t err_out_size)
{
    char url[160];
    snprintf(url, sizeof(url), "%s%s", s_base_url, path);

    s_http_response_len = 0;

    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .event_handler = http_event_handler,
        .timeout_ms = 8000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, strlen(body));

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP POST %s failed: %s", path, esp_err_to_name(err));
        if (err_out) {
            snprintf(err_out, err_out_size, "%s", esp_err_to_name(err));
        }
        return false;
    }
    if (s_http_response_buf == NULL) {
        if (err_out) {
            snprintf(err_out, err_out_size, "response buffer not allocated");
        }
        return false;
    }

    int clamp_len = s_http_response_len < (HTTP_RESPONSE_BUF_SIZE - 1) ? s_http_response_len : (HTTP_RESPONSE_BUF_SIZE - 1);
    s_http_response_buf[clamp_len] = '\0';

    if (status != expect_status) {
        ESP_LOGW(TAG, "HTTP POST %s returned status %d: %s", path, status, s_http_response_buf);
        if (err_out) {
            /* サーバーは失敗時 {"error": "..."} 形式で返すので、あればそれを使う */
            cJSON *resp = cJSON_Parse(s_http_response_buf);
            const cJSON *error_field = resp ? cJSON_GetObjectItemCaseSensitive(resp, "error") : NULL;
            if (cJSON_IsString(error_field)) {
                snprintf(err_out, err_out_size, "%s", error_field->valuestring);
            } else {
                snprintf(err_out, err_out_size, "HTTP %d", status);
            }
            if (resp) {
                cJSON_Delete(resp);
            }
        }
        return false;
    }
    return true;
}

bool conversation_client_start(const char *topic, const char *tone_key, int max_turns,
                                const char **providers, int provider_count,
                                const char *moderator,
                                char *err_out, size_t err_out_size)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "topic", topic);
    cJSON_AddStringToObject(root, "tone", tone_key);
    cJSON_AddNumberToObject(root, "max_turns", max_turns);
    cJSON_AddStringToObject(root, "moderator", moderator);

    cJSON *participants = cJSON_AddArrayToObject(root, "participants");
    for (int i = 0; i < provider_count; i++) {
        cJSON *p = cJSON_CreateObject();
        cJSON_AddStringToObject(p, "provider", providers[i]);
        const char *model = default_model_for(providers[i]);
        if (model) {
            cJSON_AddStringToObject(p, "model", model);
        } else {
            cJSON_AddNullToObject(p, "model");
        }
        cJSON_AddItemToArray(participants, p);
    }

    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (body == NULL) {
        if (err_out) {
            snprintf(err_out, err_out_size, "JSON生成に失敗しました");
        }
        return false;
    }

    bool ok = http_post_json("/api/start", body, 202, err_out, err_out_size);
    cJSON_free(body);
    return ok;
}

bool conversation_client_control(const char *action, char *err_out, size_t err_out_size)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "action", action);

    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (body == NULL) {
        if (err_out) {
            snprintf(err_out, err_out_size, "JSON生成に失敗しました");
        }
        return false;
    }

    bool ok = http_post_json("/api/control", body, 200, err_out, err_out_size);
    cJSON_free(body);
    return ok;
}

void conversation_client_poll(conversation_event_cb_t cb, void *ctx)
{
    char url[160];
    snprintf(url, sizeof(url), "%s/api/events?since=%d", s_base_url, s_since_seq);

    s_http_response_len = 0;

    esp_http_client_config_t config = {
        .url = url,
        .event_handler = http_event_handler,
        .timeout_ms = 5000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP GET failed: %s", esp_err_to_name(err));
        return;
    }
    if (status != 200) {
        ESP_LOGW(TAG, "HTTP GET returned status %d", status);
        return;
    }
    if (s_http_response_buf == NULL) {
        ESP_LOGE(TAG, "Response buffer not allocated");
        return;
    }

    int clamp_len = s_http_response_len < (HTTP_RESPONSE_BUF_SIZE - 1) ? s_http_response_len : (HTTP_RESPONSE_BUF_SIZE - 1);
    s_http_response_buf[clamp_len] = '\0';

    cJSON *root = cJSON_Parse(s_http_response_buf);
    if (root == NULL) {
        ESP_LOGE(TAG, "JSON parse failed. Raw response: %s", s_http_response_buf);
        return;
    }

    const cJSON *latest_seq = cJSON_GetObjectItemCaseSensitive(root, "latest_seq");
    if (cJSON_IsNumber(latest_seq)) {
        s_since_seq = latest_seq->valueint;
    }

    const cJSON *events = cJSON_GetObjectItemCaseSensitive(root, "events");
    const cJSON *event = NULL;
    cJSON_ArrayForEach(event, events) {
        if (cb) {
            cb(event, ctx);
        }
    }

    cJSON_Delete(root);
}
