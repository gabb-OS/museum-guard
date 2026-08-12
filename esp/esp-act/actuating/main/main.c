#include <stdio.h>
#include "esp_random.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"

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

typedef enum {
    STATE_IDLE,
    STATE_IMPACT,
    STATE_THEFT
} alarm_state_t;

void taskAlarmLedManager(void *pvParameters);
void impactTimerCallback(TimerHandle_t xTimer);

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
    
    xTaskNotify(ambientLightTaskHandle, 50, eSetValueWithOverwrite);
}

// Normalizes ESP-REC value between [0,100] in ambientLight_post_handler
void taskAmbientLight(void *pvParameters){
    uint32_t brightness;

    while(1){
        xTaskNotifyWait(0x00, 0xFFFFFFFF, &brightness, portMAX_DELAY);
        ESP_LOGI("LIGHT", "Artwork illumination change event received!");

        uint32_t duty = (brightness * 255) / 100;
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
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

        } else {
            if (state == STATE_IMPACT) {
                is_led_on = !is_led_on;
                gpio_set_level(LED_THEFT_IMPACT_PIN, is_led_on ? 1 : 0);
            }
        }
    }
}

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