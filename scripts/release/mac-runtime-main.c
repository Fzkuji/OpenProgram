#include <Python.h>
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#ifndef PYTHON_HOME_RELATIVE
#define PYTHON_HOME_RELATIVE "../../../python"
#endif
int main(int argc, char **argv) {
    char executable[PATH_MAX], resolved[PATH_MAX], candidate[PATH_MAX], home[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0 || !realpath(executable, resolved)) return 1;
    char directory[PATH_MAX];
    snprintf(directory, sizeof(directory), "%s", resolved);
    char *slash = strrchr(directory, '/');
    if (!slash) return 1;
    *slash = '\0';
    if (snprintf(candidate, sizeof(candidate), "%s/%s", directory, PYTHON_HOME_RELATIVE) >= sizeof(candidate) || !realpath(candidate, home)) return 1;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    PyStatus status = PyConfig_SetBytesString(&config, &config.home, home);
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesString(&config, &config.executable, resolved);
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    return Py_RunMain();
}
