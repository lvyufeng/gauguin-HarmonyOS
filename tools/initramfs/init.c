/*
 * Minimal init for the gauguin mainline bring-up.
 *
 * The phone has no exposed UART and no Android userspace here, so a static
 * binary that prints a hardware report to the framebuffer console is the whole
 * diagnostic interface. It never exits: it prints a heartbeat with uptime so
 * that a photograph of the screen distinguishes "booted and idle" from "hung".
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

static void read_file(const char *path, char *buf, size_t n)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        snprintf(buf, n, "<cannot open>");
        return;
    }
    ssize_t r = read(fd, buf, n - 1);
    close(fd);
    if (r < 0)
        r = 0;
    buf[r] = '\0';
    /* trim */
    while (r > 0 && (buf[r - 1] == '\n' || buf[r - 1] == '\0'))
        buf[--r] = '\0';
}

static void print_file(const char *label, const char *path)
{
    char buf[512];
    read_file(path, buf, sizeof(buf));
    printf("  %-34s %s\n", label, buf);
    fflush(stdout);
}

static void list_dir(const char *label, const char *path)
{
    DIR *d = opendir(path);
    printf("  %-34s", label);
    if (!d) {
        printf(" <missing>\n");
        fflush(stdout);
        return;
    }
    struct dirent *e;
    int n = 0;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.')
            continue;
        if (n && n % 4 == 0)
            printf("\n%*s", 37, "");
        printf("%-16s", e->d_name);
        n++;
    }
    closedir(d);
    printf(n ? "  (%d)\n" : " <empty>\n", n);
    fflush(stdout);
}

static void mount_all(void)
{
    mkdir("/proc", 0555);
    mkdir("/sys", 0555);
    mkdir("/dev", 0755);
    mkdir("/tmp", 0777);
    mount("proc", "/proc", "proc", 0, NULL);
    mount("sysfs", "/sys", "sysfs", 0, NULL);
    mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
    mount("tmpfs", "/tmp", "tmpfs", 0, NULL);
}

static void report(void)
{
    printf("\n");
    printf("========================================================\n");
    printf("  gauguin mainline bring-up: hardware report\n");
    printf("========================================================\n\n");

    print_file("kernel", "/proc/version");
    print_file("device tree model", "/proc/device-tree/model");
    print_file("device tree compatible", "/proc/device-tree/compatible");
    print_file("command line", "/proc/cmdline");
    print_file("cpu count", "/proc/cpuinfo");
    printf("\n");

    list_dir("block devices", "/dev");
    list_dir("registered block devs", "/sys/block");
    list_dir("drm cards", "/sys/class/drm");
    list_dir("regulators", "/sys/class/regulator");
    list_dir("i2c adapters", "/sys/class/i2c-adapter");
    list_dir("clk providers", "/sys/class/clock");
    printf("\n");

    /* The two facts that decide whether this boot was useful. */
    printf("  --- key results ---\n");
    char buf[512];
    read_file("/sys/block/sda/dev", buf, sizeof(buf));
    printf("  UFS (sda) present                  %s\n",
           strcmp(buf, "<cannot open>") ? "YES" : "NO");
    read_file("/sys/class/drm/card0/dev", buf, sizeof(buf));
    printf("  DRM card0 present                  %s\n",
           strcmp(buf, "<cannot open>") ? "YES" : "NO");
    read_file("/sys/class/regulator/regulator.0/name", buf, sizeof(buf));
    printf("  first regulator                    %s\n", buf);
    printf("\n");
    printf("========================================================\n");
    printf("  === BOOT OK ===\n");
    printf("========================================================\n\n");
    fflush(stdout);
}

int main(void)
{
    mount_all();
    report();

    /* Heartbeat: proves the system is alive and not wedged, and gives the
     * photograph a timestamp to prove it was taken after boot completed. */
    time_t t0 = time(NULL);
    for (unsigned i = 0;; i++) {
        time_t now = time(NULL);
        printf("\r  alive: %5lu s uptime, heartbeat %u   ",
               (unsigned long)(now - t0), i);
        fflush(stdout);
        sleep(5);
    }
    return 0;
}
