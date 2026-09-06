#ifndef GPS_H
#define GPS_H

#include "driver/uart.h"
#include <stdbool.h>

// Initializes the UART to the GPS module. Returns true if successful.
bool init_gps(uart_port_t uart_num, int tx_pin, int rx_pin, int baud_rate);

// Reads the last position from the GPS module (lat/lon in decimal degrees).
// Returns true only if a valid fix has been received.
bool read_gps(float *lat, float *lon);

#endif