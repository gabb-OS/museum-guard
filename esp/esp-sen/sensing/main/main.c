#include <stdio.h>
#include <math.h>
#include <coap3/coap.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdint.h>
#include "driver/gpio.h"
#include "esp_log.h"
#include "driver/adc.h"
#include "nvs_flash.h"
#include "mylib/accelerometer.h"
#include "mylib/wifi.h"
#include "mylib/coap_server.h"
#include "mylib/gps.h"

//Wifi 
#define WIFI_SSID          "ProgettoIoT"
#define WIFI_PASSWORD       "PasswordIoT"

// GPS
#define GPS_UART_NUM   UART_NUM_1
#define GPS_TX_PIN     GPIO_NUM_17
#define GPS_RX_PIN     GPIO_NUM_16
#define GPS_BAUD_RATE  9600
#define GPS_PING_INTERVAL_MS 5000

// stato di allarme globale
static bool g_theft_alarm = false;
static SemaphoreHandle_t g_alarm_mutex;

// mutex dedicato allo stato del rilevamento furto (baseline + counter),
// cceduto anche dall'handler di reset CoAP, non solo dalla task accelerometro
static SemaphoreHandle_t g_theft_mutex;

//photoresistor stuff
#define LIGHT_SENS ADC1_CHANNEL_6
#define LIGHT_RAW_MIN             200
#define LIGHT_RAW_MAX             3500
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
#define IMPACT_THRESHOLD    0.3f
//THEFT
#define THEFT_DISPLACEMENT_THRESHOLD 0.5f   // soglia di spostamento significativo
#define THEFT_CONFIRM_SAMPLES        20     // persistenza richiesta per confermare
static float baseline_ay = 0;
static bool  baseline_set = false;
static int   theft_counter = 0;

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

void calibrate_theft_baseline(float ay) {
    xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
    baseline_ay = ay;
    baseline_set = true;
    xSemaphoreGive(g_theft_mutex);
}

void read_accelerometer_sensor(void *pvParameters) {
    float ax = 0, ay = 0, az = 0;
    float last_ax = 0, last_ay = 0, last_az = 0;
    float diff_x, diff_y;
    read_accel(&last_ax, &last_ay, &last_az);  // prima lettura per inizializzare i "last"
    calibrate_theft_baseline(last_ay);
    while (1) {
        if (read_accel(&ax, &ay, &az)) {
            diff_x = ax - last_ax; //backword differencies for first derivative
            diff_y = ay - last_ay; //backword differencies for first derivative

            printf("ax=%.3f ay=%.3f az=%.3f | diff_x=%.3f diff_y=%.3f\n",
                   ax, ay, az, diff_x, diff_y);

            if (fabs(diff_x) > IMPACT_THRESHOLD) {
                char event_buf[64];
                int elen = snprintf(event_buf, sizeof(event_buf),
                                    "{\"type\":\"impact\",\"axis\":\"x\",\"value\":%.3f}", diff_x);
                coap_push_event(event_buf, elen);
            }

            xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
            if (baseline_set) {
                float displacement = ay - baseline_ay;
                if (fabs(displacement) > THEFT_DISPLACEMENT_THRESHOLD) {
                    theft_counter++;
                    if (theft_counter >= THEFT_CONFIRM_SAMPLES) {
                        char event_buf[64];
                        int elen = snprintf(event_buf, sizeof(event_buf),
                                            "{\"type\":\"theft\",\"axis\":\"y\",\"value\":%.3f}", displacement);
                        coap_push_event(event_buf, elen);

                        xSemaphoreTake(g_alarm_mutex, portMAX_DELAY);
                        g_theft_alarm = true;
                        xSemaphoreGive(g_alarm_mutex);
                    }
                } else {
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

// Task: mentre l'allarme furto è attivo, legge il GPS e invia la posizione come evento
void gps_ping_task(void *pvParameters) {
    while (1) {
        bool alarm;
        xSemaphoreTake(g_alarm_mutex, portMAX_DELAY);
        alarm = g_theft_alarm;
        xSemaphoreGive(g_alarm_mutex);

        if (alarm) {
            float lat, lon;
            if (read_gps(&lat, &lon)) {
                char event_buf[64];
                int elen = snprintf(event_buf, sizeof(event_buf),
                                    "{\"type\":\"position\",\"lat\":%.6f,\"lon\":%.6f}", lat, lon);
                coap_push_event(event_buf, elen);
            }
            vTaskDelay(pdMS_TO_TICKS(GPS_PING_INTERVAL_MS));
        } else {
            vTaskDelay(pdMS_TO_TICKS(500)); // poll più rado quando non c'è allarme
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

// Handler PUT: resetta l'allarme furto e ricalibra la baseline
static void hnd_put_reset_alarm(coap_resource_t *resource, coap_session_t *session, const coap_pdu_t *request, const coap_string_t *query, coap_pdu_t *response) {
    xSemaphoreTake(g_alarm_mutex, portMAX_DELAY);
    g_theft_alarm = false;
    xSemaphoreGive(g_alarm_mutex);

    xSemaphoreTake(g_theft_mutex, portMAX_DELAY);
    theft_counter = 0;
    xSemaphoreGive(g_theft_mutex);

    xSemaphoreTake(g_accel_mutex, portMAX_DELAY);
    float current_ay = g_avg_ay;
    xSemaphoreGive(g_accel_mutex);
    calibrate_theft_baseline(current_ay);

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
    g_alarm_mutex = xSemaphoreCreateMutex();

    //Inizializzazione server coap
    if (!coap_server_setup()) {
        ESP_LOGE("MAIN", "CoAP setup failed");
    }
    coap_register_get_resource("light", hnd_get_light);
    coap_register_get_resource("accel", hnd_get_accel); 
    coap_register_observable_resource("events", hnd_get_events);
    coap_register_put_resource("reset_alarm", hnd_put_reset_alarm);

    xTaskCreate(coap_server_task, "coap_task", 4096, NULL, 5, NULL);
    xTaskCreate(read_accelerometer_sensor, "acceleromete_task", 2048, NULL, 5, NULL);
    xTaskCreate(read_light_sensor, "light_task", 2048, NULL, 4, NULL);
    xTaskCreate(gps_ping_task, "gps_task", 4096, NULL, 4, NULL);
    xTaskCreate(accel_avg_task, "accel_avg_task", 2048, NULL, 3, NULL);
}