#include "gps.h"
#include "esp_log.h"
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <math.h>

#define GPS_BUF_SIZE 512

#define GPS_FALLBACK_LAT 44.497008f
#define GPS_FALLBACK_LON 11.355927f

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
    // SEGREZZA: Una coordinata NMEA valida deve essere lunga almeno 5 caratteri (es. "00.00")
    // Questo blocca i frammenti di buffer corrotti che generavano "0.425"
    if (!raw || strlen(raw) < 5) return 0.0f; 
    
    float value = atof(raw);
    int degrees = (int)(value / 100);
    float minutes = value - degrees * 100;
    return degrees + minutes / 60.0f;
}

bool read_gps(float *lat, float *lon) {
    int len = uart_read_bytes(g_uart_num, (uint8_t *)g_line_buf, sizeof(g_line_buf) - 1, pdMS_TO_TICKS(1000));
    if (len <= 0) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Nessun dato UART, uso coordinate di fallback");
        return true;
    }
    g_line_buf[len] = '\0';

    char *gga = strstr(g_line_buf, "GGA");
    if (!gga) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Nessuna sentence GGA trovata, uso coordinate di fallback");
        return true;
    }

    if (gga - g_line_buf < 3) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Sentence troncata, uso coordinate di fallback");
        return true;
    }

    char *sentence = gga - 3;
    if (sentence[0] != '$') {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Sentence malformata, uso coordinate di fallback");
        return true;
    }

    char *line_end = strpbrk(sentence, "\r\n");
    int line_len = line_end ? (int)(line_end - sentence) : (int)strlen(sentence);
    ESP_LOGI(TAG, "NMEA raw: %.*s", line_len, sentence);

    char *fields[15] = {0};
    int nfields = 0;
    char sentence_copy[256];
    strncpy(sentence_copy, sentence, sizeof(sentence_copy) - 1);
    sentence_copy[sizeof(sentence_copy) - 1] = '\0';

    char *tok = strtok(sentence_copy, ",");
    while (tok && nfields < 15) {
        fields[nfields++] = tok;
        tok = strtok(NULL, ",");
    }

    if (nfields < 7) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Sentence GGA incompleta, uso coordinate di fallback");
        return true;
    }

    if (strlen(fields[6]) != 1) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Fix quality mancante, uso coordinate di fallback");
        return true;
    }
    char fix_quality = fields[6][0];
    if (fix_quality < '1' || fix_quality > '6') {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Nessun fix (quality=%c), uso coordinate di fallback", fix_quality);
        return true;
    }

    if (strlen(fields[2]) == 0 || strlen(fields[4]) == 0) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Campi coordinata vuoti, uso coordinate di fallback");
        return true;
    }

    float lat_val = nmea_to_decimal(fields[2]);
    if (fields[3][0] == 'S') lat_val = -lat_val;

    float lon_val = nmea_to_decimal(fields[4]);
    if (fields[5][0] == 'W') lon_val = -lon_val;

    if (lat_val == 0.0f && lon_val == 0.0f) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Coordinate Null Island, uso coordinate di fallback");
        return true;
    }
    if (fabs(lat_val) > 90.0f || fabs(lon_val) > 180.0f) {
        *lat = GPS_FALLBACK_LAT;
        *lon = GPS_FALLBACK_LON;
        ESP_LOGW(TAG, "Coordinate fuori range, uso coordinate di fallback");
        return true;
    }

    *lat = lat_val;
    *lon = lon_val;
    return true;
}