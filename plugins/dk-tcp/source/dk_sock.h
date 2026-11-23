#ifndef DK_SOCK_H
#define DK_SOCK_H

#include <stdint.h>

/* Called from mcp_main. Creates the listen thread the same way the WUP
 * server does. iosCreateThread from the FS process returns -4. */
int dk_net_start(void);

/* FS logger line when the MCP thread's phase changes. Safe to call often. */
void dk_net_log_fs(void);

/* Blocking exchange with the connected PC. Returns 0 and sets *resp_len on
 * success. Returns -1 when no PC is connected or the socket fails. */
int dk_net_exchange(const void *req, uint32_t req_len,
                    void *resp, uint32_t resp_cap, uint32_t *resp_len);

#endif
