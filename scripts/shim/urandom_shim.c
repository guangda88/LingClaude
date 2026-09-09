// urandom_shim.c ｜ 编译: gcc -shared -fPIC -o urandom_shim.so urandom_shim.c -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>      /* O_CREAT — 文档版缺此头 */
#include <sys/types.h>  /* mode_t — 文档版缺此头 */
#include <string.h>
#include <stdarg.h>
#include <sys/syscall.h>
#include <unistd.h>

static int is_random_path(const char *p) {
    return p && (strcmp(p, "/dev/urandom") == 0 || strcmp(p, "/dev/random") == 0);
}

int open(const char *path, int flags, ...) {
    static int (*real_open)(const char *, int, ...) = NULL;
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    if (is_random_path(path)) {
        va_list ap; va_start(ap, flags);
        mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
        va_end(ap);
        int fd = (int)syscall(SYS_memfd_create, "urandom_shim", 0);
        if (fd < 0) return real_open(path, flags, mode);  // 兜底
        unsigned char buf[4096];
        syscall(SYS_getrandom, buf, sizeof(buf), 0);
        if (write(fd, buf, sizeof(buf)) > 0) lseek(fd, 0, SEEK_SET);
        return fd;  // 持续随机需求可改为周期回填
    }
    va_list ap; va_start(ap, flags);
    mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
    va_end(ap);
    return real_open(path, flags, mode);
}

/* ---- 变体覆盖: open64 / openat / openat64 (glibc 内部 open 多走 openat) ---- */
int open64(const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags);
    mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
    va_end(ap);
    return open(path, flags, mode);  /* 复用已拦截的 open */
}
int openat(int dirfd, const char *path, int flags, ...) {
    static int (*real_openat)(int, const char *, int, ...) = NULL;
    if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
    if (is_random_path(path)) return open(path, flags, 0);
    va_list ap; va_start(ap, flags);
    mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
    va_end(ap);
    return real_openat(dirfd, path, flags, mode);
}
int openat64(int dirfd, const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags);
    mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
    va_end(ap);
    return openat(dirfd, path, flags, mode);
}
