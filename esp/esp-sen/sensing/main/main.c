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
#define WIFI_SSID          "ProgettoIoT"
#define WIFI_PASSWORD       "PasswordIoT"
// GPS
#define GPS_UART_NUM   UART_NUM_1
#define GPS_TX_PIN     GPIO_NUM_17
#define GPS_RX_PIN     GPIO_NUM_16
#define GPS_BAUD_RATE  9600
#define GPS_PING_INTERVAL_MS 5000

// stato di allarme globale
static bool g_tracking_active = false;
static SemaphoreHandle_t g_tracking_mutex;

// mutex dedicato allo stato del rilevamento furto (baseline + counter),
// acceduto anche dall'handler di reset CoAP, non solo dalla task accelerometro
static SemaphoreHandle_t g_theft_mutex;
//photoresistor stuff
#define LIGHT_SENS ADC1_CHANNEL_6
#define LIGHT_RAW_MIN             1000
#define LIGHT_RAW_MAX             4095
static float g_light_percent = 0.0f;
SemaphoreHandle_t g_light_mutex;

//accelerometer stuff
#define SDA_ACC_SENS GPIO_NUM_21
#define SCL_ACC_SENS GPIO_NUM_22
#define I2C_MASTER_NUM      I2C_NUM_0
#define I2C_MASTER_FREQ_HZ  400000
static float sum_ax = 0, sum_ay = 0, sum_az = 0;
static int sample_count = 0;
static SemaphoreHandle_t g_accel_mutex;
static float g_avg_ax = 0, g_avg_ay = 0, g_avg_az = 0; 

// non piu define: servono configurabili a runtime (bonus soglie)
static float g_impact_threshold = 0.4f;
static float g_theft_displacement_threshold = 0.35f;
static SemaphoreHandle_t g_threshold_mutex;
#define THRESHOLD_MIN 0.05f
#define THRESHOLD_MAX 5.00f

//THEFT
#define THEFT_CONFIRM_SAMPLES        6     // persistenza richiesta per confermare
static float baseline_az = 0;   // asse verticale, sensore montato dritto
static bool  baseline_set = false;
static int   theft_counter = 0;
#define THEFT_COOLDOWN_MS 3000
static int64_t last_theft_trigger_us = 0;
void read_light_sensor(void *pvParameters) {
    while (1) {
        int raw = adc1_get_raw(LIGHT_SENS);
        float percent;  // nome diverso dalla globale
        if (raw <= LIGHT_RAW_MIN) {
            percent = 0.0f;
        } else if (raw >= LIGHT_RAW_MAX) {
            percent = 100.0f;
        } else {
            percent = (float)(raw - LIGHT_RAW_MIN) / (LIGHT_RAW_MAX - LIGHT_RAW_MIN) * 100.0f;
        }

        printf("Luce: raw=%d, %.1f%%\n", raw, percent);
        // Scrivo il valore nella variabile condivisa, protetto da mutex
        xSemaphoreTake(g_light_mutex, portMAX_DELAY);
        g_light_percent = percent;
        xSemaphoreGive(g_light_mutex);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void calibrate_theft_baseline(float az) {
    xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
    baseline_az = az;
    baseline_set = true;
    xSemaphoreGive(g_theft_mutex);
}

void read_accelerometer_sensor(void *pvParameters) {
    float ax = 0, ay = 0, az = 0;
    float last_ax = 0, last_ay = 0, last_az = 0;
    float diff_x, diff_y;
    read_accel(&last_ax, &last_ay, &last_az);  // prima lettura per inizializzare i "last"
    calibrate_theft_baseline(last_az); 
    while (1) {
        if (read_accel(&ax, &ay, &az)) {
            diff_x = ax - last_ax; //backword differencies for first derivative
            diff_y = ay - last_ay; //backword differencies for first derivative
            printf("ax=%.3f ay=%.3f az=%.3f | diff_x=%.3f diff_y=%.3f\n",
                   ax, ay, az, diff_x, diff_y);
            // impact resta sull'asse longitudinale (x)
            xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
            float impact_th = g_impact_threshold;
            xSemaphoreGive(g_threshold_mutex);
            if (fabs(diff_x) > impact_th) {
                char event_buf[64];
                int elen = snprintf(event_buf, sizeof(event_buf),
                                    "{\"type\":\"impact\",\"axis\":\"x\",\"value\":%.3f}", diff_x);
                coap_push_event(event_buf, elen);
            }
            xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
            float theft_th = g_theft_displacement_threshold;
            xSemaphoreGive(g_threshold_mutex);
            xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
            if (baseline_set) {
                float displacement = az - baseline_az;   // theft sull'asse verticale, non piu x
                if (fabs(displacement) > theft_th) {
                    theft_counter += 2;
                    if (theft_counter > 50) theft_counter = 50;
                } else {
                    theft_counter -= 1;
                    if (theft_counter < 0) theft_counter = 0;
                }
                if (theft_counter >= THEFT_CONFIRM_SAMPLES) {
                    int64_t now_us = esp_timer_get_time();
                    if (now_us - last_theft_trigger_us > (int64_t)THEFT_COOLDOWN_MS * 1000) {
                        char event_buf[64];
                        int elen = snprintf(event_buf, sizeof(event_buf),
                                            "{\"type\":\"theft\",\"axis\":\"z\",\"value\":%.3f}", displacement);
                        coap_push_event(event_buf, elen);
                        // fa partire solo il gps, non e' piu uno stato di allarme condiviso
                        xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
                        g_tracking_active = true;
                        xSemaphoreGive(g_tracking_mutex);
                        last_theft_trigger_us = now_us;
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
    while (1) {
        xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
        if (sample_count > 0) {
            g_avg_ax = sum_ax / sample_count;
            g_avg_ay = sum_ay / sample_count;
            g_avg_az = sum_az / sample_count;
        }
        printf("Media 1s: ax=%.3f ay=%.3f az=%.3f (n=%d)\n",g_avg_ax, g_avg_ay, g_avg_az, sample_count);
        sum_ax = sum_ay = sum_az = 0;
        sample_count = 0;
        xSemaphoreGive(g_accel_mutex);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

// Task: dopo un furto confermato pinga il gps, finche' non arriva un reset
// (non e' piu legata a un "allarme" generico, solo al tracking post-furto)
void gps_ping_task(void *pvParameters) {
    while (1) {
        bool tracking;
        xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
        tracking = g_tracking_active;
        xSemaphoreGive(g_tracking_mutex);
        if (tracking) {
            float lat, lon;
            if (read_gps(&lat, &lon)) {
                ESP_LOGI("GPS_TASK", "Fix GPS: lat=%.6f lon=%.6f", lat, lon);
                char event_buf[64];
                int elen = snprintf(event_buf, sizeof(event_buf),
                                    "{\"type\":\"position\",\"lat\":%.6f,\"lon\":%.6f}", lat, lon);
                coap_push_event(event_buf, elen);
            } else {
                ESP_LOGW("GPS_TASK", "Nessun fix GPS disponibile");
            }
            vTaskDelay(pdMS_TO_TICKS(GPS_PING_INTERVAL_MS));
        } else {
            vTaskDelay(pdMS_TO_TICKS(500));
        }
    }
}

//coap heandlers
static void hnd_get_light(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    float value;
    xSemaphoreTake(g_light_mutex, portMAX_DELAY);
    value = g_light_percent;
    xSemaphoreGive(g_light_mutex);
    char buf[16];
    int len = snprintf(buf, sizeof(buf), "%.1f", value);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT); // 2.05
    coap_add_data(response, len, (const uint8_t *)buf);
}

static void hnd_get_accel(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    float ax, ay, az;
    xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
    ax = g_avg_ax;
    ay = g_avg_ay;
    az = g_avg_az;
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

// GET /thresholds: soglie correnti, entrambe insieme
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

// PUT /thresholds/impact: payload plain text, un solo float, niente json
static void hnd_put_impact_threshold(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    size_t data_len = 0;
    const uint8_t *data = NULL;
    coap_get_data(request, &data_len, &data);
    if (data == NULL || data_len == 0 || data_len > 15) {
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    char buf[16];
    memcpy(buf, data, data_len);
    buf[data_len] = '\0';
    float value = strtof(buf, NULL);
    if (value < THRESHOLD_MIN || value > THRESHOLD_MAX) {
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
    g_impact_threshold = value;
    xSemaphoreGive(g_threshold_mutex);
    ESP_LOGI("THRESHOLDS", "impact aggiornata: %.3f", value);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED); // 2.04
}

// PUT /thresholds/theft: stesso discorso, ma sulla soglia di spostamento furto
static void hnd_put_theft_threshold(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    size_t data_len = 0;
    const uint8_t *data = NULL;
    coap_get_data(request, &data_len, &data);
    if (data == NULL || data_len == 0 || data_len > 15) {
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    char buf[16];
    memcpy(buf, data, data_len);
    buf[data_len] = '\0';
    float value = strtof(buf, NULL);
    if (value < THRESHOLD_MIN || value > THRESHOLD_MAX) {
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    xSemaphoreTake(g_threshold_mutex, portMAX_DELAY);
    g_theft_displacement_threshold = value;
    xSemaphoreGive(g_threshold_mutex);
    ESP_LOGI("THRESHOLDS", "theft aggiornata: %.3f", value);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED); // 2.04
}

// Handler PUT: resetta il tracking gps e ricalibra la baseline
static void hnd_put_reset_alarm(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    xSemaphoreTake(g_tracking_mutex, portMAX_DELAY);
    g_tracking_active = false;
    xSemaphoreGive(g_tracking_mutex);
    xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
    theft_counter = 0;
    xSemaphoreGive(g_theft_mutex);
    xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
    float current_az = g_avg_az;
    xSemaphoreGive(g_accel_mutex);
    calibrate_theft_baseline(current_az);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED); // 2.04
}

void app_main(void){
    // Init NVS (richiesto dal WiFi per salvare config)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    wifi_init_sta(WIFI_SSID, WIFI_PASSWORD);// blocca finché non sei connesso
     if (!init_gps(GPS_UART_NUM, GPS_TX_PIN, GPS_RX_PIN, GPS_BAUD_RATE)) {
        ESP_LOGE("MAIN", "GPS init failed");
    }
    // Inizializzazione photoresistor
    adc1_config_width(ADC_WIDTH_BIT_12);
    adc1_config_channel_atten(LIGHT_SENS, ADC_ATTEN_DB_11);
    g_light_mutex = xSemaphoreCreateMutex();
    // Inizializzazione accelerometer
    if (!init_accelerometer(SDA_ACC_SENS, SCL_ACC_SENS)) {
        ESP_LOGE("MAIN", "Accelerometer init failed");
    }
    g_accel_mutex = xSemaphoreCreateMutex();
    g_theft_mutex = xSemaphoreCreateMutex();
    g_tracking_mutex = xSemaphoreCreateMutex();
    g_threshold_mutex = xSemaphoreCreateMutex();
    //Inizializzazione server coap
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