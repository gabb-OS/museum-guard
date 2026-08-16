#ifndef GPS_H
#define GPS_H

#include "driver/uart.h"
#include <stdbool.h>

// Inizializza la UART verso il modulo GPS. Ritorna true se ok.
bool init_gps(uart_port_t uart_num, int tx_pin, int rx_pin, int baud_rate);

// Legge l'ultima posizione dal modulo GPS (lat/lon in gradi decimali).
// Ritorna true solo se è stato ricevuto un fix valido.
bool read_gps(float *lat, float *lon);

#endif