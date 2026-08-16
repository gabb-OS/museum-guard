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

// Da chiamare dalla task che rileva l'evento (es. accelerometro)
bool coap_push_event(const void *data, size_t len);

// Da chiamare dentro l'handler GET di /events per leggere l'ultimo evento salvato
size_t coap_get_last_event(uint8_t *out, size_t max_len);

#endif