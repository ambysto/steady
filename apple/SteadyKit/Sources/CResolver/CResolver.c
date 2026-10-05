// The resolver state API (res_ninit/res_getservers) has no Swift interface; this wraps it.
#include "CResolver.h"

#include <arpa/inet.h>
#include <resolv.h>
#include <string.h>

int steady_dns_servers(char *buffer, int stride, int max) {
    struct __res_state state;
    memset(&state, 0, sizeof state);
    if (res_ninit(&state) != 0) {
        res_ndestroy(&state);   // res_ninit may have allocated before failing
        return -1;
    }
    union res_sockaddr_union servers[16];
    int found = res_getservers(&state, servers, 16);
    int written = 0;
    for (int i = 0; i < found && written < max; i++) {
        char *slot = buffer + written * stride;
        const char *ok = NULL;
        if (servers[i].sin.sin_family == AF_INET) {
            ok = inet_ntop(AF_INET, &servers[i].sin.sin_addr, slot, (socklen_t)stride);
        } else if (servers[i].sin6.sin6_family == AF_INET6) {
            ok = inet_ntop(AF_INET6, &servers[i].sin6.sin6_addr, slot, (socklen_t)stride);
        }
        if (ok != NULL) {
            written++;
        }
    }
    res_ndestroy(&state);
    return written;
}
