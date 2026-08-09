#include <stdio.h>
#include "esp_random.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "shared.h"

static const char *E_TAG = "MAIN";
TaskHandle_t ambientLighTaskHandle = NULL;
TaskHandle_t impactTaskHandle = NULL;
TaskHandle_t theftTaskHandle = NULL;

SemaphoreHandle_t ambientLightMutex = NULL;
int ambientBrightness = 20;

static httpd_handle_t server = NULL;

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
    gpio_set_direction(LED_AMBIENT_PIN, GPIO_MODE_OUTPUT);
    ambientLightMutex = xSemaphoreCreateMutex();
    //int localBrightness = 20;

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
    xTaskCreate(taskAmbientLight, "Ambiental Light", 2048, NULL, 1, &ambientLighTaskHandle);
    xTaskCreate(taskImpactLight, "Impact Warning", 2048, NULL, 4, &impactTaskHandle);
    xTaskCreate(taskTheftLight, "Theft Warning", 2048, NULL, 5, &theftTaskHandle);
    
}

void taskAmbientLight(void *pvParameters){
    // non mi piace dichiarata qua dentro
    int localBrightness = 20;

    while(1){
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI("LIGHT", "Artwork illumination change event received!");

        if (xSemaphoreTake(ambientLightMutex, portMAX_DELAY) == pdTRUE){
            localBrightness = ambientBrightness;
            xSemaphoreGive(ambientLightMutex);
        }


        uint32_t duty = (localBrightness * 255) / 100;
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);


    }

}

void taskImpactLight(void *pvParameters){
    while(1){
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI("IMPACT", "Impact event received!");

        // 20 seconds blinking
        TickType_t alarmEnd = xTaskGetTickCount() + pdMS_TO_TICKS(20000);
        while (xTaskGetTickCount() < alarmEnd) {
            gpio_set_level(LED_THEFT_IMPACT_PIN, 1);
            vTaskDelay(pdMS_TO_TICKS(800));
            gpio_set_level(LED_THEFT_IMPACT_PIN, 0);
            vTaskDelay(pdMS_TO_TICKS(200));
        }
    }
}

void taskTheftLight(void *pvParameters){
    while(1){
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI("THEFT", "Theft event received!");

        gpio_set_level(LED_THEFT_IMPACT_PIN, 1);
    }
}

