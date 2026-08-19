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

/* ---------------- Alarm state type ---------------- */
typedef enum {
    STATE_IDLE,
    STATE_IMPACT,
    STATE_THEFT
} alarm_state_t;


/* ---------------- Handle condivisi (definiti in main.c) ---------------- */
extern SemaphoreHandle_t ambientLightMutex;
extern SemaphoreHandle_t alarmLightStateMutex;
extern TaskHandle_t ambientLightTaskHandle;
extern TaskHandle_t impactTaskHandle;
extern TaskHandle_t theftTaskHandle;
extern TaskHandle_t resetTaskHandle;

/* ---------------- Shared Vars ---------------- */
extern int ambientBrightness;

/* ---------------- Task entry point (definiti in main.c) ---------------- */
void taskAmbientLight(void *pvParameters);
void taskImpactLight(void *pvParameters);
void taskTheftLight(void *pvParameters);
void taskResetLight(void *pvParameters);

/* ---------------- State accessors ---------------- */
int get_current_brightness(void);
alarm_state_t get_current_alarm_state(void);
const char* alarm_state_to_string(alarm_state_t state);



/* ---------------- Networking (definiti in networkConnect.c) ---------------- */
void wifi_init_sta(void);
httpd_handle_t start_webserver(void);

#endif