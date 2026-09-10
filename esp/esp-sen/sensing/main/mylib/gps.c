#include "gps.h"
#include "esp_log.h"
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <math.h>

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
    if (len <= 0) return false;
    g_line_buf[len] = '\0';

    char *gga = strstr(g_line_buf, "GGA");
    if (!gga) return false;

    if (gga - g_line_buf < 3) {
        return false; // Sentence troncata
    }

    char *sentence = gga - 3;
    if (sentence[0] != '$') {
        return false;
    }

    char *fields[15] = {0};
    int nfields = 0;
    // Usiamo una copia della stringa per strtok perché strtok modifica l'originale
    char sentence_copy[256];
    strncpy(sentence_copy, sentence, sizeof(sentence_copy) - 1);
    sentence_copy[sizeof(sentence_copy) - 1] = '\0';

    char *tok = strtok(sentence_copy, ",");
    while (tok && nfields < 15) {
        fields[nfields++] = tok;
        tok = strtok(NULL, ",");
    }

    if (nfields < 7) return false;

    // VALIDAZIONE FIX QUALITY BLINDATA:
    // 0 = Invalid, 1 = GPS Fix, 2 = DGPS, 3 = PPS, 4 = RTK, 5 = Float RTK, 6 = Estimated
    if (strlen(fields[6]) != 1) return false;
    char fix_quality = fields[6][0];
    if (fix_quality < '1' || fix_quality > '6') {
        return false; // Rifiuta esplicitamente '0' o caratteri non validi
    }

    // Se i campi coordinata sono vuoti o solo zeri, scarta
    if (strlen(fields[2]) == 0 || strlen(fields[4]) == 0) return false;

    float lat_val = nmea_to_decimal(fields[2]);
    if (fields[3][0] == 'S') lat_val = -lat_val;
    
    float lon_val = nmea_to_decimal(fields[4]);
    if (fields[5][0] == 'W') lon_val = -lon_val;

    // PROTEZIONE "NULL ISLAND" E SANITY CHECK
    // Se le coordinate sono esattamente 0,0 o fuori dai limiti terrestri, è un fix falso
    if (lat_val == 0.0f && lon_val == 0.0f) return false;
    if (fabs(lat_val) > 90.0f || fabs(lon_val) > 180.0f) return false;

    *lat = lat_val;
    *lon = lon_val;
    return true;
}