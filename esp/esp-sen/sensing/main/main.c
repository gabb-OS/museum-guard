#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <coap3/coap.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdint.h>
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/adc.h"
#include "nvs_flash.h"
#include "mylib/accelerometer.h"
#include "mylib/wifi.h"
#include "mylib/coap_server.h"
#include "mylib/gps.h"

// Wifi 
#define WIFI_SSID          CONFIG_WIFI_SSID
#define WIFI_PASSWORD      CONFIG_WIFI_PASSWORD
// GPS
#define GPS_UART_NUM   UART_NUM_1
#define GPS_TX_PIN     GPIO_NUM_17
#define GPS_RX_PIN     GPIO_NUM_16
#define GPS_BAUD_RATE  9600
#define GPS_PING_INTERVAL_MS 5000
#define GPS_WARMUP_INTERVAL_MS 2000  

// stato di allarme globale
static bool g_tracking_active = false;
static SemaphoreHandle_t g_tracking_mutex;

// mutex dedicato allo stato del rilevamento furto (baseline + counter)
static SemaphoreHandle_t g_theft_mutex;

// photoresistor stuff
#define LIGHT_SENS ADC1_CHANNEL_6
#define LIGHT_RAW_MIN             350
#define LIGHT_RAW_MAX             3800
static float g_light_percent = 0.0f;
SemaphoreHandle_t g_light_mutex;

// accelerometer stuff
#define SDA_ACC_SENS GPIO_NUM_21
#define SCL_ACC_SENS GPIO_NUM_22
#define I2C_MASTER_NUM      I2C_NUM_0
#define I2C_MASTER_FREQ_HZ  400000
static float sum_ax = 0, sum_ay = 0, sum_az = 0;
static int sample_count = 0;
static SemaphoreHandle_t g_accel_mutex;
static float g_avg_ax = 0, g_avg_ay = 0, g_avg_az = 0; 

// soglie configurabili a runtime
static float g_impact_threshold = 0.4f;
static float g_theft_displacement_threshold = 0.25f;
static SemaphoreHandle_t g_threshold_mutex;
#define THRESHOLD_MIN 0.05f
#define THRESHOLD_MAX 5.00f

// THEFT
#define THEFT_CONFIRM_SAMPLES        6
static float baseline_ax = 0;
static bool  baseline_set = false;
static int   theft_counter = 0;
#define THEFT_COOLDOWN_MS 3000
static int64_t last_theft_trigger_us = 0;

void read_light_sensor(void *pvParameters) {
    while (1) {
        int raw = adc1_get_raw(LIGHT_SENS);
        float percent;
        if (raw <= LIGHT_RAW_MIN) {
            percent = 0.0f;
        } else if (raw >= LIGHT_RAW_MAX) {
            percent = 100.0f;
        } else {
            percent = (float)(raw - LIGHT_RAW_MIN) / (LIGHT_RAW_MAX - LIGHT_RAW_MIN) * 100.0f;
        }

        xSemaphoreTake(g_light_mutex, portMAX_DELAY);
        g_light_percent = percent;
        xSemaphoreGive(g_light_mutex);
        
        // Stampa deploy: luce e stato ogni 1 secondo
        xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
        bool is_tracking = g_tracking_active;
        xSemaphoreGive(g_tracking_mutex);
        ESP_LOGI("STATUS", "Luce: %.1f%% | Stato: %s", percent, is_tracking ? "TRACKING" : "IDLE");
        
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void calibrate_theft_baseline(float ax) {
    xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
    baseline_ax = ax;
    baseline_set = true;
    xSemaphoreGive(g_theft_mutex);
}

void read_accelerometer_sensor(void *pvParameters) {
    float ax = 0, ay = 0, az = 0;
    float last_ax = 0, last_ay = 0, last_az = 0;
    float diff_x, diff_y, diff_z;
    read_accel(&last_ax, &last_ay, &last_az);
    calibrate_theft_baseline(last_ax); 
    
    while (1) {
        if (read_accel(&ax, &ay, &az)) {
            diff_x = ax - last_ax;
            diff_y = ay - last_ay;
            diff_z = az - last_az;
            
            // Sopprime i warning di variabile inutilizzata (utile se in futuro vorrai usarle)
            (void)diff_x;
            (void)diff_y;

            // --- IMPACT ---
            xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
            float impact_th = g_impact_threshold;
            xSemaphoreGive(g_threshold_mutex);
            
            if (fabs(diff_z) > impact_th) {
                char event_buf[64];
                int elen = snprintf(event_buf, sizeof(event_buf),
                                    "{\"type\":\"impact\",\"axis\":\"z\",\"value\":%.3f}", diff_z);
                coap_push_event(event_buf, elen);
                ESP_LOGW("EVENT", "IMPATTO rilevato (diff_z: %.3f)", diff_z);
            }
            
            // --- THEFT ---
            xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
            float theft_th = g_theft_displacement_threshold;
            xSemaphoreGive(g_threshold_mutex);
            
            xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
            if (baseline_set) {
                float displacement = ax - baseline_ax;
                
                if (fabs(displacement) > theft_th) {
                    theft_counter += 2;
                    if (theft_counter > 50) theft_counter = 50;
                } else if (fabs(displacement) < (theft_th * 0.4f)) {
                    theft_counter -= 1;
                    if (theft_counter < 0) theft_counter = 0;
                }
                
                if (theft_counter >= THEFT_CONFIRM_SAMPLES) {
                    int64_t now_us = esp_timer_get_time();
                    if (now_us - last_theft_trigger_us > (int64_t)THEFT_COOLDOWN_MS * 1000) {
                        char event_buf[64];
                        int elen = snprintf(event_buf, sizeof(event_buf),
                                            "{\"type\":\"theft\",\"axis\":\"x\",\"value\":%.3f}", displacement);
                        coap_push_event(event_buf, elen);
                        
                        xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
                        g_tracking_active = true;
                        xSemaphoreGive(g_tracking_mutex);
                        last_theft_trigger_us = now_us;
                        
                        ESP_LOGE("EVENT", "FURTO confermato (spostamento: %.3f)", displacement);
                    }
                    theft_counter = 0;
                }
            }
            xSemaphoreGive(g_theft_mutex);
            
            last_ax = ax;
            last_ay = ay;
            last_az = az;
            
            xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
            sum_ax += ax;
            sum_ay += ay;
            sum_az += az;
            sample_count++;
            xSemaphoreGive(g_accel_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void accel_avg_task(void *pvParameters) {
    int print_counter = 0;
    while (1) {
        xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
        if (sample_count > 0) {
            g_avg_ax = sum_ax / sample_count;
            g_avg_ay = sum_ay / sample_count;
            g_avg_az = sum_az / sample_count;
        }
        
        sum_ax = sum_ay = sum_az = 0;
        sample_count = 0;
        xSemaphoreGive(g_accel_mutex);
        
        // Stampa l'accelerazione media ogni 1 secondo (4 cicli da 250ms)
        print_counter++;
        if (print_counter >= 4) {
            ESP_LOGI("ACCEL", "Media: ax=%.3f ay=%.3f az=%.3f", g_avg_ax, g_avg_ay, g_avg_az);
            print_counter = 0;
        }
        
        vTaskDelay(pdMS_TO_TICKS(250)); 
    }
}

void gps_ping_task(void *pvParameters) {
    while (1) {
        bool tracking;
        xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
        tracking = g_tracking_active;
        xSemaphoreGive(g_tracking_mutex);

        float lat, lon;
        if (read_gps(&lat, &lon)) {
            if (tracking) {
                // Stampa GPS solo quando il tracking è attivo (furto in corso)
                ESP_LOGI("GPS", "Posizione: lat=%.6f, lon=%.6f", lat, lon);
                char event_buf[64];
                int elen = snprintf(event_buf, sizeof(event_buf),
                                    "{\"type\":\"position\",\"lat\":%.6f,\"lon\":%.6f}", lat, lon);
                coap_push_event(event_buf, elen);
            }
        }

        vTaskDelay(pdMS_TO_TICKS(tracking ? GPS_PING_INTERVAL_MS : GPS_WARMUP_INTERVAL_MS));
    }
}

// --- COAP HANDLERS ---

static void hnd_get_light(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    float value;
    xSemaphoreTake(g_light_mutex, portMAX_DELAY);
    value = g_light_percent;
    xSemaphoreGive(g_light_mutex);
    char buf[16];
    int len = snprintf(buf, sizeof(buf), "%.1f", value);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data(response, len, (const uint8_t *)buf);
}

static void hnd_get_accel(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    float ax, ay, az;
    xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
    ax = g_avg_ax; ay = g_avg_ay; az = g_avg_az;
    xSemaphoreGive(g_accel_mutex);
    char buf[64];
    int len = snprintf(buf, sizeof(buf), "{\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f}", ax, ay, az);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data(response, len, (const uint8_t *)buf);
}

static void hnd_get_events(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    uint8_t buf[64];
    size_t len = coap_get_last_event(buf, sizeof(buf));
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    if (len > 0) {
        coap_add_data(response, len, buf);
    }
}

static void hnd_get_thresholds(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    float impact, theft_disp;
    xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
    impact = g_impact_threshold;
    theft_disp = g_theft_displacement_threshold;
    xSemaphoreGive(g_threshold_mutex);
    char buf[80];
    int len = snprintf(buf, sizeof(buf), "{\"impact\":%.3f,\"theft_displacement\":%.3f}", impact, theft_disp);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data(response, len, (const uint8_t *)buf);
}

static void hnd_put_impact_threshold(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    size_t data_len = 0;
    const uint8_t *data = NULL;
    coap_get_data(request, &data_len, &data);

    ESP_LOGI("DIAG", "PUT /thresholds/impact ricevuto: data_len=%d, data='%.*s'", 
             (int)data_len, (int)data_len, data ? (const char*)data : "NULL");

    if (data == NULL || data_len == 0 || data_len > 15) {
        ESP_LOGW("DIAG", "Payload non valido (NULL, vuoto o troppo lungo)");
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    char buf[16];
    memcpy(buf, data, data_len);
    buf[data_len] = '\0';
    float value = strtof(buf, NULL);

    ESP_LOGI("DIAG", "Valore parsato con strtof: %.6f", value);

    if (value < THRESHOLD_MIN || value > THRESHOLD_MAX) {
        ESP_LOGW("DIAG", "Valore %.3f fuori range [%.2f, %.2f]", value, THRESHOLD_MIN, THRESHOLD_MAX);
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
    g_impact_threshold = value;
    xSemaphoreGive(g_threshold_mutex);
    ESP_LOGI("CONFIG", "Soglia impatto aggiornata: %.3f", value);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED);
}

static void hnd_put_theft_threshold(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    size_t data_len = 0;
    const uint8_t *data = NULL;
    coap_get_data(request, &data_len, &data);

    ESP_LOGI("DIAG", "PUT /thresholds/theft ricevuto: data_len=%d, data='%.*s'", 
             (int)data_len, (int)data_len, data ? (const char*)data : "NULL");

    if (data == NULL || data_len == 0 || data_len > 15) {
        ESP_LOGW("DIAG", "Payload non valido (NULL, vuoto o troppo lungo)");
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    char buf[16];
    memcpy(buf, data, data_len);
    buf[data_len] = '\0';
    float value = strtof(buf, NULL);

    ESP_LOGI("DIAG", "Valore parsato con strtof: %.6f", value);

    if (value < THRESHOLD_MIN || value > THRESHOLD_MAX) {
        ESP_LOGW("DIAG", "Valore %.3f fuori range [%.2f, %.2f]", value, THRESHOLD_MIN, THRESHOLD_MAX);
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
    g_theft_displacement_threshold = value;
    xSemaphoreGive(g_threshold_mutex);
    ESP_LOGI("CONFIG", "Soglia furto aggiornata: %.3f", value);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED);
}

static void hnd_put_reset_alarm(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    ESP_LOGI("DIAG", "PUT /reset_alarm ricevuto");
    xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
    g_tracking_active = false;
    xSemaphoreGive(g_tracking_mutex);
    
    xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
    theft_counter = 0;
    xSemaphoreGive(g_theft_mutex);
    
    xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
    float current_ax = g_avg_ax;
    xSemaphoreGive(g_accel_mutex);
    
    calibrate_theft_baseline(current_ax);
    
    ESP_LOGI("SYSTEM", "Reset allarme: tracking disattivato e baseline ricalibrata.");
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED);
}

void app_main(void){
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    
    wifi_init_sta(WIFI_SSID, WIFI_PASSWORD);
    
    if (!init_gps(GPS_UART_NUM, GPS_TX_PIN, GPS_RX_PIN, GPS_BAUD_RATE)) {
        ESP_LOGE("MAIN", "GPS init failed");
    }
    
    adc1_config_width(ADC_WIDTH_BIT_12);
    adc1_config_channel_atten(LIGHT_SENS, ADC_ATTEN_DB_12);
    
    if (!init_accelerometer(SDA_ACC_SENS, SCL_ACC_SENS)) {
        ESP_LOGE("MAIN", "Accelerometer init failed");
    }
    
    g_light_mutex = xSemaphoreCreateMutex();
    g_accel_mutex = xSemaphoreCreateMutex();
    g_theft_mutex = xSemaphoreCreateMutex();
    g_tracking_mutex = xSemaphoreCreateMutex();
    g_threshold_mutex = xSemaphoreCreateMutex();
    
    if (!coap_server_setup()) {
        ESP_LOGE("MAIN", "CoAP setup failed");
    }
    
    coap_register_get_resource("light", hnd_get_light);
    coap_register_get_resource("accel", hnd_get_accel); 
    coap_register_observable_resource("events", hnd_get_events);
    coap_register_put_resource("reset_alarm", hnd_put_reset_alarm);
    coap_register_get_resource("thresholds", hnd_get_thresholds);
    coap_register_put_resource("thresholds/impact", hnd_put_impact_threshold);
    coap_register_put_resource("thresholds/theft", hnd_put_theft_threshold);
    
    xTaskCreate(coap_server_task, "coap_task", 4096, NULL, 5, NULL);
    xTaskCreate(read_accelerometer_sensor, "acceleromete_task", 4096, NULL, 5, NULL);
    xTaskCreate(read_light_sensor, "light_task", 2048, NULL, 4, NULL);
    xTaskCreate(gps_ping_task, "gps_task", 4096, NULL, 4, NULL);
    xTaskCreate(accel_avg_task, "accel_avg_task", 2048, NULL, 3, NULL);
}