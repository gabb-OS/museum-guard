#ifndef SHARED_H
#define SHARED_H

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_http_server.h"

/* ---------------- GPIO pin mapping ---------------- */
#define LED_AMBIENT_PIN             GPIO_NUM_33
#define LED_THEFT_IMPACT_PIN        GPIO_NUM_32

/* ---------------- Misc ---------------- */
#define BLOCKING_TICKS       pdMS_TO_TICKS(100)

/* ---------------- Handle condivisi (definiti in main.c) ---------------- */
extern SemaphoreHandle_t ambientLightMutex;
extern TaskHandle_t ambientLighTaskHandle;
extern TaskHandle_t impactTaskHandle;
extern TaskHandle_t theftTaskHandle;

/* ---------------- Shared Vars ---------------- */
extern int ambientBrightness;

/* ---------------- Task entry point (definiti in main.c) ---------------- */
void taskAmbientLight(void *pvParameters);
void taskImpactLight(void *pvParameters);
void taskTheftLight(void *pvParameters);

/* ---------------- Networking (definiti in networkConnect.c) ---------------- */
void wifi_init_sta(void);
httpd_handle_t start_webserver(void);

#endif