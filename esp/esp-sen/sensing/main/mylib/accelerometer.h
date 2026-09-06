#ifndef ACCELEROMETER_H
#define ACCELEROMETER_H

#include "driver/gpio.h"
#include <stdbool.h>

// Initialize the I2C bus + wake up the sensor. Return true if OK.
bool init_accelerometer(gpio_num_t sda_pin, gpio_num_t scl_pin);

// Read acceleration in g. Returns true if the I2C read was successful.
bool read_accel(float *ax, float *ay, float *az);

#endif