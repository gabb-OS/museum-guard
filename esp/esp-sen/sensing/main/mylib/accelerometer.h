#ifndef ACCELEROMETER_H
#define ACCELEROMETER_H

#include "driver/gpio.h"
#include <stdbool.h>

// Inizializza bus I2C + sveglia il sensore. Ritorna true se ok.
bool init_accelerometer(gpio_num_t sda_pin, gpio_num_t scl_pin);

// Legge accelerazione in g. Ritorna true se la lettura I2C è andata a buon fine.
bool read_accel(float *ax, float *ay, float *az);

#endif