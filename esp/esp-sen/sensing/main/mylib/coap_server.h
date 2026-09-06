#ifndef COAP_SERVER_H
#define COAP_SERVER_H

#include <stdbool.h>
#include <stddef.h>
#include <coap3/coap.h>

bool coap_server_setup(void);
void coap_server_task(void *pvParameters);
void coap_register_get_resource(const char *path, coap_method_handler_t handler);
void coap_register_observable_resource(const char *path, coap_method_handler_t handler);
void coap_register_put_resource(const char *path, coap_method_handler_t handler);

// To be called from the task that detects the event 
bool coap_push_event(const void *data, size_t len);

// Call inside the /events GET handler to read the last saved event
size_t coap_get_last_event(uint8_t *out, size_t max_len);

#endif