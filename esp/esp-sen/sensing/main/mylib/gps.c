#include "gps.h"
#include "esp_log.h"
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define GPS_BUF_SIZE 512

static const char *TAG = "GPS";
static uart_port_t g_uart_num;
static char g_line_buf[GPS_BUF_SIZE];

// Initializes the UART to the GPS module. Returns true if successful.
bool init_gps(uart_port_t uart_num, int tx_pin, int rx_pin, int baud_rate) {
    g_uart_num = uart_num;
    uart_config_t cfg = {
        .baud_rate = baud_rate,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
    };
    if (uart_param_config(uart_num, &cfg) != ESP_OK) return false;
    if (uart_set_pin(uart_num, tx_pin, rx_pin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK) return false;
    if (uart_driver_install(uart_num, GPS_BUF_SIZE * 2, 0, 0, NULL, 0) != ESP_OK) return false;
    return true;
}

// Converts an NMEA coordinate (ddmm.mmmm) to decimal degrees
static float nmea_to_decimal(const char *raw) {
    float value = atof(raw);
    int degrees = (int)(value / 100);
    float minutes = value - degrees * 100;
    return degrees + minutes / 60.0f;
}

// Reads from the UART and looks for a complete $GxGGA phrase; if there is a valid fix, returns lat/lon
bool read_gps(float *lat, float *lon) {
    int len = uart_read_bytes(g_uart_num, (uint8_t *)g_line_buf, sizeof(g_line_buf) - 1, pdMS_TO_TICKS(1000));
    if (len <= 0) return false;
    g_line_buf[len] = '\0';

    char *gga = strstr(g_line_buf, "GGA");
    if (!gga) return false;

    // "$XXGGA" -> servono almeno 3 caratteri prima di "GGA" per il talker ID + '$'.
    // Se il chunk UART inizia a meta' sentence, scartiamo: aspettiamo il prossimo giro.
    if (gga - g_line_buf < 3) {
        ESP_LOGW(TAG, "Sentence GGA troncata a inizio buffer, scarto questo giro");
        return false;
    }

    char *sentence = gga - 3;
    if (sentence[0] != '$') {
        ESP_LOGW(TAG, "Header sentence non valido, scarto");
        return false;
    }

    char *fields[15] = {0};
    int nfields = 0;
    char *tok = strtok(sentence, ",");
    while (tok && nfields < 15) {
        fields[nfields++] = tok;
        tok = strtok(NULL, ",");
    }

    if (nfields < 7 || strlen(fields[2]) == 0 || strlen(fields[4]) == 0) {
        return false;
    }

    // Validazione robusta del campo fix quality: deve essere un singolo digit,
    // non un atoi() su una stringa potenzialmente sporca.
    if (strlen(fields[6]) != 1 || !isdigit((unsigned char)fields[6][0]) || fields[6][0] == '0') {
        return false; // nessun fix
    }

    float lat_val = nmea_to_decimal(fields[2]);
    if (fields[3][0] == 'S') lat_val = -lat_val;
    float lon_val = nmea_to_decimal(fields[4]);
    if (fields[5][0] == 'W') lon_val = -lon_val;

    *lat = lat_val;
    *lon = lon_val;
    return true;
}