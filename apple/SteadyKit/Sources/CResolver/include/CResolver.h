#ifndef CRESOLVER_H
#define CRESOLVER_H

/// Writes the system's DNS servers (primary first) as numeric addresses into `buffer`, one per
/// `stride` bytes, at most `max` of them. Returns how many were written, or -1 when the resolver
/// configuration cannot be read. IPv6 scope ids are dropped.
int steady_dns_servers(char *buffer, int stride, int max);

#endif
