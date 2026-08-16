idf_component_register(
    SRCS "main.c" "mylib/accelerometer.c" "mylib/wifi.c" "mylib/coap_server.c" "mylib/gps.c"
    INCLUDE_DIRS "." "mylib"
    REQUIRES driver esp_timer log coap esp_wifi esp_netif esp_event nvs_flash
)