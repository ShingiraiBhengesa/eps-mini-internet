
{
  "devices": {
    "host1": {
      "virtual_address": "host1",
      "role": "host",
      "udp_host": "127.0.0.1",
      "udp_port": 5001,
      "neighbors": ["routerA"]
    },
    "routerA": {
      "virtual_address": "routerA",
      "role": "router",
      "udp_host": "127.0.0.1",
      "udp_port": 5002,
      "neighbors": ["host1"]
    }
  }
}
