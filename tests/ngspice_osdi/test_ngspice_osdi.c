/* Quick test: load libngspice, load OSDI model, run a simple DC sim.
 *
 * Pipeline: test_resistor.va -> OpenVAF -> test_resistor.osdi -> libngspice
 *
 * Build:
 *   openvaf test_resistor.va -o test_resistor.osdi
 *   gcc -o test_ngspice_osdi test_ngspice_osdi.c -lngspice
 *
 * Run:
 *   ./test_ngspice_osdi
 *
 * Expected: DC sweep V=0..5, I = -V/1000 (negative = into source)
 *
 * Notes:
 *   - OSDI device prefix in netlists is 'N' (not Y or R)
 *   - OSDI module names must not clash with built-in device names
 *     (e.g. "resistor" clashes with built-in "Resistor", use "myresistor")
 *   - osdi command must be issued before sourcing the netlist
 */
#include <stdio.h>
#include <stdbool.h>
#include <string.h>
#include <libgen.h>
#include <ngspice/sharedspice.h>

static int ng_getchar(char *outputreturn, int ident, void *userdata)
{
    (void)ident; (void)userdata;
    printf("[ngspice] %s\n", outputreturn);
    return 0;
}

static int ng_getstat(char *outputreturn, int ident, void *userdata)
{
    (void)ident; (void)userdata;
    printf("[status] %s\n", outputreturn);
    return 0;
}

static int ng_exit(int exitstatus, NG_BOOL unloading, NG_BOOL quit, int ident, void *userdata)
{
    (void)exitstatus; (void)unloading; (void)quit; (void)ident; (void)userdata;
    return 0;
}

int main(int argc, char *argv[])
{
    (void)argc;
    /* Determine directory containing this executable for finding test files */
    char dirbuf[4096];
    strncpy(dirbuf, argv[0], sizeof(dirbuf) - 1);
    dirbuf[sizeof(dirbuf) - 1] = '\0';
    char *dir = dirname(dirbuf);

    char osdi_cmd[4096], source_cmd[4096];
    snprintf(osdi_cmd, sizeof(osdi_cmd), "osdi %s/test_resistor.osdi", dir);
    snprintf(source_cmd, sizeof(source_cmd), "source %s/test_resistor.spice", dir);

    int ret = ngSpice_Init(ng_getchar, ng_getstat, ng_exit,
                           NULL, NULL, NULL, NULL);
    printf("ngSpice_Init returned %d\n", ret);

    /* Load OSDI model first (must be before sourcing netlist) */
    ngSpice_Command(osdi_cmd);

    /* Source netlist that references the OSDI model */
    ngSpice_Command(source_cmd);
    ngSpice_Command("run");
    ngSpice_Command("print i(v1)");

    return 0;
}
