#include <stdio.h>
#include "esp_random.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include <math.h>
#include "shared.h"

static const char *E_TAG = "MAIN";

TaskHandle_t ambientLightTaskHandle = NULL;
TaskHandle_t ledManagerTaskHandle = NULL;
TaskHandle_t impactTaskHandle = NULL;
TaskHandle_t theftTaskHandle = NULL;
TaskHandle_t resetTaskHandle = NULL;

TimerHandle_t impactTimer = NULL;

static httpd_handle_t server = NULL;

#define CMD_BIT_RESET  (1 << 0)     // Bit 0: 0001
#define CMD_BIT_THEFT  (1 << 1)     // Bit 1: 0010
#define CMD_BIT_IMPACT (1 << 2)     // Bit 2: 0100
#define CMD_BIT_TIMEOUT (1 << 3)    // Bit 2: 1000

SemaphoreHandle_t ambientLightMutex = NULL;
SemaphoreHandle_t alarmLightStateMutex = NULL;
static int g_currentBrightness = 0;
static alarm_state_t g_alarmState = STATE_IDLE;

void taskAlarmLedManager(void *pvParameters);
void impactTimerCallback(TimerHandle_t xTimer);

 
/* ---------------- State accessors thread-safe ---------------- */
int get_current_brightness(void)
{
    int value;
    xSemaphoreTake(ambientLightMutex, portMAX_DELAY);
    value = g_currentBrightness;
    xSemaphoreGive(ambientLightMutex);
    return value;
}
 
alarm_state_t get_current_alarm_state(void)
{
    alarm_state_t value;
    xSemaphoreTake(alarmLightStateMutex, portMAX_DELAY);
    value = g_alarmState;
    xSemaphoreGive(alarmLightStateMutex);
    return value;
}
 
const char* alarm_state_to_string(alarm_state_t state)
{
    switch (state) {
        case STATE_IMPACT: return "IMPACT";
        case STATE_THEFT:  return "THEFT";
        case STATE_IDLE:
        default:           return "IDLE";
    }
}


/************* Main *************/
void app_main(void)
{
    /* Network/webserver initialization*/
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }

    ESP_ERROR_CHECK(ret);
    wifi_init_sta();

    server = start_webserver();
    if (server == NULL) {
        ESP_LOGE(E_TAG, "Server start failed");
    }

    /* Initialization */
    gpio_set_direction(LED_THEFT_IMPACT_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(LED_THEFT_IMPACT_PIN, 0);

    gpio_set_direction(LED_AMBIENT_PIN, GPIO_MODE_OUTPUT);

    ambientLightMutex = xSemaphoreCreateMutex();
    alarmLightStateMutex = xSemaphoreCreateMutex();

    impactTimer = xTimerCreate(
        "ImpactTmr",
        pdMS_TO_TICKS(20000),
        pdFALSE,
        (void *)0,
        impactTimerCallback
    );

    ledc_timer_config_t timer_conf = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .timer_num       = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_8_BIT,   // 0-255
        .freq_hz         = 5000,
        .clk_cfg         = LEDC_AUTO_CLK
    };
    ledc_timer_config(&timer_conf);
 
    ledc_channel_config_t channel_conf = {
        .gpio_num   = LED_AMBIENT_PIN,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel    = LEDC_CHANNEL_0,
        .timer_sel  = LEDC_TIMER_0,
        .duty       = 0,
        .hpoint     = 0
    };
    ledc_channel_config(&channel_conf);


    /* Task Creation */
    xTaskCreate(taskAmbientLight, "Ambiental Light", 2048, NULL, 1, &ambientLightTaskHandle);
    xTaskCreate(taskAlarmLedManager, "Alarm LED Mgr", 2048, NULL, 5, &ledManagerTaskHandle);
    xTaskCreate(taskImpactLight, "Impact Warning", 2048, NULL, 3, &impactTaskHandle);
    xTaskCreate(taskTheftLight, "Theft Warning", 2048, NULL, 4, &theftTaskHandle);
    xTaskCreate(taskResetLight, "Lights reset", 2048, NULL, 4, &resetTaskHandle);
    
    xTaskNotify(ambientLightTaskHandle, 30, eSetValueWithOverwrite);
}

// Normalizes value between [0,100] in ambientLight_post_handler
void taskAmbientLight(void *pvParameters){
    uint32_t brightness;

    while(1){
        xTaskNotifyWait(0x00, 0xFFFFFFFF, &brightness, portMAX_DELAY);
        ESP_LOGI("LIGHT", "Artwork illumination change event received! (Raw: %lu%%)", brightness);

        uint32_t duty = 0;

        if (brightness == 0) {
            duty = 0; // Spento completamente solo se richiesto esplicitamente
        } else {
            // 1. Mappiamo l'intervallo 1-100 in 0.0-1.0 per la curva gamma
            float normalized = (brightness - 1) / 99.0f;
            float gamma_corrected = powf(normalized, 2.2f);
            
            // 2. Definiamo una soglia minima di duty cycle (es. 15 su 255 = ~6% di luminosità)
            // Questo impedisce al LED di spegnersi sotto il 15% di input.
            // Se lo vuoi ancora più luminoso al minimo, alza questo valore a 20 o 25.
            uint32_t min_duty = 15; 
            
            // 3. Riscaliamo il risultato della curva gamma tra min_duty e 255
            duty = min_duty + (uint32_t)(gamma_corrected * (255.0f - min_duty));
        }

        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);

        xSemaphoreTake(ambientLightMutex, portMAX_DELAY);
        g_currentBrightness = brightness;
        xSemaphoreGive(ambientLightMutex);
    }
}

void taskAlarmLedManager(void *pvParameters) {
    alarm_state_t state = STATE_IDLE;
    uint32_t notified_bits; 
    bool is_led_on = false;

    while (1) {
        TickType_t wait_time = portMAX_DELAY;

        if (state == STATE_IMPACT) {
            wait_time = is_led_on ? pdMS_TO_TICKS(800) : pdMS_TO_TICKS(200);
        }

        BaseType_t notification_received = xTaskNotifyWait(
            0x00,           
            0xFFFFFFFF,      
            &notified_bits,
            wait_time   
        );

        if (notification_received == pdTRUE) {
            
            if (notified_bits & CMD_BIT_RESET) {
                state = STATE_IDLE;
                gpio_set_level(LED_THEFT_IMPACT_PIN, 0);
                xTimerStop(impactTimer, 0); 
            } 

            else if (notified_bits & CMD_BIT_THEFT) {
                state = STATE_THEFT;
                gpio_set_level(LED_THEFT_IMPACT_PIN, 1);
                xTimerStop(impactTimer, 0); 
            } 
            
            else if (notified_bits & CMD_BIT_IMPACT) {
                if (state != STATE_THEFT) {
                    state = STATE_IMPACT;
                    is_led_on = true;
                    gpio_set_level(LED_THEFT_IMPACT_PIN, 1);
                    xTimerStart(impactTimer, 0); 
                }
            }
            
            else if (notified_bits & CMD_BIT_TIMEOUT) {
                if (state == STATE_IMPACT) {
                    state = STATE_IDLE;
                    gpio_set_level(LED_THEFT_IMPACT_PIN, 0);
                }
            }

            xSemaphoreTake(alarmLightStateMutex, portMAX_DELAY);
            g_alarmState = state;
            xSemaphoreGive(alarmLightStateMutex);

        } else {
            if (state == STATE_IMPACT) {
                is_led_on = !is_led_on;
                gpio_set_level(LED_THEFT_IMPACT_PIN, is_led_on ? 1 : 0);
            }
        }
    }
}

/*  Technically speaking, those 3 tasks are not necessary and occupy resorces
    This decoupling is made to give a logical structure and 
    to prepare the bed for future added features in the single tasks */
void taskImpactLight(void *pvParameters){

    while(1){
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI("IMPACT", "Impact event received!");
        xTaskNotify(ledManagerTaskHandle, CMD_BIT_IMPACT, eSetBits);
    }
}

void taskTheftLight(void *pvParameters){

    while(1){
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI("THEFT", "Theft event received!");
        xTaskNotify(ledManagerTaskHandle, CMD_BIT_THEFT, eSetBits);
    }
}

void taskResetLight(void *pvParameters){

    while(1){
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI("RESET", "Reset alarm light received!");
        xTaskNotify(ledManagerTaskHandle, CMD_BIT_RESET, eSetBits);
    }
}

void impactTimerCallback(TimerHandle_t xTimer) {
    xTaskNotify(ledManagerTaskHandle, CMD_BIT_TIMEOUT, eSetBits);
}