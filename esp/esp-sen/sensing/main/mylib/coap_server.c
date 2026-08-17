#include "coap_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <netinet/in.h>
#include <string.h>

#define COAP_EVENT_MAX_LEN 64

typedef struct {
    uint8_t data[COAP_EVENT_MAX_LEN];
    size_t len;
} coap_event_msg_t;

static const char *TAG = "COAP_SERVER";
static coap_context_t *g_coap_ctx = NULL;
static QueueHandle_t g_event_queue = NULL;
static TaskHandle_t g_coap_task_handle = NULL;
static coap_resource_t *g_events_resource = NULL;

static uint8_t g_last_event_data[COAP_EVENT_MAX_LEN];
static size_t g_last_event_len = 0;

bool coap_server_setup(void) {
    coap_startup();

    g_coap_ctx = coap_new_context(NULL);
    if (!g_coap_ctx) {
        ESP_LOGE(TAG, "Failed to create context");
        return false;
    }

    coap_address_t serv_addr;
    coap_address_init(&serv_addr);
    serv_addr.addr.sin.sin_family = AF_INET;
    serv_addr.addr.sin.sin_addr.s_addr = INADDR_ANY;
    serv_addr.addr.sin.sin_port = htons(COAP_DEFAULT_PORT);

    coap_endpoint_t *ep = coap_new_endpoint(g_coap_ctx, &serv_addr, COAP_PROTO_UDP);
    if (!ep) {
        ESP_LOGE(TAG, "Failed to create endpoint");
        return false;
    }

    g_event_queue = xQueueCreate(5, sizeof(coap_event_msg_t));
    if (!g_event_queue) {
        ESP_LOGE(TAG, "Failed to create event queue");
        return false;
    }

    return true;
}

void coap_register_get_resource(const char *path, coap_method_handler_t handler) {
    coap_resource_t *r = coap_resource_init(coap_make_str_const(path), 0);
    coap_register_request_handler(r, COAP_REQUEST_GET, handler);
    coap_add_resource(g_coap_ctx, r);
}

void coap_register_observable_resource(const char *path, coap_method_handler_t handler) {
    coap_resource_t *r = coap_resource_init(coap_make_str_const(path), COAP_RESOURCE_FLAGS_NOTIFY_CON);
    coap_register_request_handler(r, COAP_REQUEST_GET, handler);
    coap_resource_set_get_observable(r, 1);
    coap_add_resource(g_coap_ctx, r);
    g_events_resource = r;
}

bool coap_push_event(const void *data, size_t len) {
    if (len > COAP_EVENT_MAX_LEN || !g_event_queue) return false;

     ESP_LOGI(TAG, "Event pushed: %.*s", (int)len, (const char *)data);

    coap_event_msg_t msg;
    memcpy(msg.data, data, len);
    msg.len = len;

    if (xQueueSend(g_event_queue, &msg, 0) != pdTRUE) {
        ESP_LOGW(TAG, "Event queue full, dropping event");
        return false;
    }
    if (g_coap_task_handle) {
        xTaskNotifyGive(g_coap_task_handle);
    }
    return true;
}

size_t coap_get_last_event(uint8_t *out, size_t max_len) {
    size_t n = g_last_event_len < max_len ? g_last_event_len : max_len;
    memcpy(out, g_last_event_data, n);
    return n;
}

void coap_server_task(void *pvParameters) {
    g_coap_task_handle = xTaskGetCurrentTaskHandle();

    while (1) {
        coap_io_process(g_coap_ctx, 50);

        uint32_t notif = ulTaskNotifyTake(pdTRUE, 0);
        if (notif > 0) {
            coap_event_msg_t msg;
            while (xQueueReceive(g_event_queue, &msg, 0) == pdTRUE) {
                ESP_LOGI(TAG, "Notifying observers: %.*s", (int)msg.len, (const char *)msg.data);
                memcpy(g_last_event_data, msg.data, msg.len);
                g_last_event_len = msg.len;
                if (g_events_resource) {
                    coap_resource_notify_observers(g_events_resource, NULL);
                }
            }
        }
    }
}

void coap_register_put_resource(const char *path, coap_method_handler_t handler) {
    coap_resource_t *r = coap_resource_init(coap_make_str_const(path), 0);
    coap_register_request_handler(r, COAP_REQUEST_PUT, handler);
    coap_add_resource(g_coap_ctx, r);
}