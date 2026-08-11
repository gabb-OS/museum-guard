#include <stdio.h>
#include <string.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "esp_err.h"

#include "driver/temperature_sensor.h"
#include "esp_http_server.h"
#include "cJSON.h"

#include "shared.h"

// CUSTOMIZE
#define WIFI_SSID          "Gaperol"
#define WIFI_PASSWORD      "s3ssoEsamb4"

#define DEVICE_ID           "ESP_ACT"

static const char *TAG = "ESP32_WEB_SERVER";

static EventGroupHandle_t wifi_event_group;
static const int WIFI_CONNECTED_BIT = BIT0;

/* ---------------- Wi-Fi management ---------------- */
static void wifi_event_handler(void *arg,
                               esp_event_base_t event_base,
                               int32_t event_id,
                               void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "Wi-Fi started, connecting...");
        esp_wifi_connect();

    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected, retrying...");
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
        esp_wifi_connect();

    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

void wifi_init_sta(void)
{
    wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,
                                               ESP_EVENT_ANY_ID,
                                               &wifi_event_handler,
                                               NULL));

    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,
                                               IP_EVENT_STA_GOT_IP,
                                               &wifi_event_handler,
                                               NULL));

    wifi_config_t wifi_config = { 0 };

    strncpy((char *)wifi_config.sta.ssid,
            WIFI_SSID,
            sizeof(wifi_config.sta.ssid) - 1);

    strncpy((char *)wifi_config.sta.password,
            WIFI_PASSWORD,
            sizeof(wifi_config.sta.password) - 1);

    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Waiting for Wi-Fi connection...");
    xEventGroupWaitBits(wifi_event_group,
                        WIFI_CONNECTED_BIT,
                        pdFALSE,
                        pdTRUE,
                        portMAX_DELAY);
}

/* ---------------- Helper functions ---------------- */

char* create_json(void)
{
    cJSON *root = cJSON_CreateObject();

    cJSON_AddStringToObject(root, "id", DEVICE_ID);

    char *json_string = cJSON_PrintUnformatted(root);

    cJSON_Delete(root);

    return json_string; 
}


/* ---------------- HTTP server ---------------- */

static esp_err_t root_get_handler(httpd_req_t *req)
{
    const char *resp =
        "ESP32 Web Server is running.\n";

    httpd_resp_set_type(req, "text/plain");
    httpd_resp_sendstr(req, resp);
    return ESP_OK;
}

// BE AWARE: this is still to check
static esp_err_t ambientLight_post_handler(httpd_req_t *req)
{
    char buf[128];
    int total_len = req->content_len;
 
    if (total_len <= 0 || total_len >= (int)sizeof(buf)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Payload mancante o troppo grande");
        return ESP_FAIL;
    }

    int received = httpd_req_recv(req, buf, total_len);
    if (received <= 0) {
        if (received == HTTPD_SOCK_ERR_TIMEOUT) {
            httpd_resp_send_408(req);
        }
        return ESP_FAIL;
    }
    buf[received] = '\0';
    int brightness = 0;
 
    ESP_LOGI(TAG, "Notifica ambient ricevuta: %s", buf);
 
    bool valid = false;
    
    cJSON *json = cJSON_Parse(buf);
    if (json != NULL) {
        cJSON *b = cJSON_GetObjectItem(json, "brightness");
        if (cJSON_IsNumber(b)) {
            brightness = b->valueint;
            valid = true;
        }
        cJSON_Delete(json);
    }



    if (valid) {
        if (brightness < 0)   brightness = 0;
        if (brightness > 100) brightness = 100;
 
        if (xSemaphoreTake(ambientLightMutex, portMAX_DELAY) == pdTRUE) {
            ambientBrightness = brightness;
            xSemaphoreGive(ambientLightMutex);
        }

        xTaskNotifyGive(ambientLighTaskHandle);
 
        httpd_resp_set_type(req, "application/json");
        httpd_resp_sendstr(req, "{\"status\":\"ok\"}");
    } else {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                             "JSON non valido, atteso {\"brightness\": 0-100}");
    }
 
    return ESP_OK;

}

static esp_err_t impact_post_handler(httpd_req_t *req)
{
    xTaskNotifyGive(impactTaskHandle);
    char *resp = create_json();

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, resp);
    free(resp);
    return ESP_OK;
}

static esp_err_t theft_post_handler(httpd_req_t *req)
{
    xTaskNotifyGive(theftTaskHandle);
    char *resp = create_json();

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, resp);
    free(resp);
    return ESP_OK;
}

static esp_err_t reset_post_handler(httpd_req_t *req)
{
    xTaskNotifyGive(resetTaskHandle);
    char *resp = create_json();

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, resp);
    free(resp);
    return ESP_OK;
}



httpd_handle_t start_webserver(void)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;

    httpd_handle_t server_handle = NULL;
    if (httpd_start(&server_handle, &config) == ESP_OK) {
        httpd_uri_t root_uri = {
            .uri       = "/",
            .method    = HTTP_GET,
            .handler   = root_get_handler,
            .user_ctx  = NULL
        };

        httpd_uri_t ambientLight_uri = {
            .uri       = "/ambientlight",
            .method    = HTTP_POST,
            .handler   = ambientLight_post_handler,
            .user_ctx  = NULL
        };


        httpd_uri_t impact_uri = {
            .uri       = "/impact",
            .method    = HTTP_POST,
            .handler   = impact_post_handler,
            .user_ctx  = NULL
        };

        httpd_uri_t theft_uri = {
            .uri       = "/theft",
            .method    = HTTP_POST,
            .handler   = theft_post_handler,
            .user_ctx  = NULL
        };

        httpd_uri_t reset_uri = {
            .uri       = "/reset",
            .method    = HTTP_POST,
            .handler   = reset_post_handler,
            .user_ctx  = NULL
        };


        httpd_register_uri_handler(server_handle, &root_uri);
        httpd_register_uri_handler(server_handle, &ambientLight_uri);
        httpd_register_uri_handler(server_handle, &impact_uri);
        httpd_register_uri_handler(server_handle, &theft_uri);
        httpd_register_uri_handler(server_handle, &reset_uri);
        
        ESP_LOGI(TAG, "Web server started");
        return server_handle;
    }

    ESP_LOGE(TAG, "Failed to start web server");
    return NULL;
}