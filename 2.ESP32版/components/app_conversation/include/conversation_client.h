#ifndef _CONVERSATION_CLIENT_H
#define _CONVERSATION_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include "cJSON.h"

/* イベント1件を受け取るコールバック。
 * event は conversation_client_poll() の呼び出し中のみ有効(呼び出し後は解放される)。
 * type/seq/speaker/text等の具体的なフィールドは呼び出し側でcJSONから取り出す。 */
typedef void (*conversation_event_cb_t)(const cJSON *event, void *ctx);

/* base_url例: "http://192.168.0.10:5000" (末尾にスラッシュを付けない) */
void conversation_client_init(const char *base_url);

/* 1回ポーリングする(since=は内部で自動追跡)。新着イベントの数だけcbを呼ぶ。
 * 通信エラーやJSONパース失敗時は何もせず戻る(呼び出し側はループで再試行すればよい)。 */
void conversation_client_poll(conversation_event_cb_t cb, void *ctx);

/* /api/start にPOSTして会話を開始する。
 * providers: "manus"/"openai"/"claude"/"zai" のうち2〜4個。モデルは各プロバイダの
 * 既定値(DEFAULT_MODEL、高速・低コスト側)を自動的に使う(端末側ではモデル選択しない)。
 * 成功(HTTP 202)ならtrue。失敗時、err_out/err_out_sizeが指定されていればサーバーからの
 * エラーメッセージ(またはHTTPステータス)を書き込む。 */
bool conversation_client_start(const char *topic, const char *tone_key, int max_turns,
                                const char **providers, int provider_count,
                                const char *moderator,
                                char *err_out, size_t err_out_size);

/* /api/control にPOSTする。action は "pause"/"resume"/"stop"。成功(HTTP 200)ならtrue。 */
bool conversation_client_control(const char *action, char *err_out, size_t err_out_size);

#endif
