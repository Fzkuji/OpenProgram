/*
 * The launcher of a managed install: picks a sealed version and starts it.
 *
 * It never changes through a self-update, so everything it reads is a frozen
 * protocol: the `current` pointer, `files.tsv`, `boot/<id>.attempts` and
 * `pending-rollback`. Built per platform at release time and shipped signed.
 *
 *   launcher [--root <root>] <python arguments...>
 *
 * Without --root the root is the parent of the `identity` directory holding
 * this executable. Steps:
 *
 *   1. developer.json present: follow it and do nothing else.
 *   2. Read `current`; restore from `current.prev`, else run
 *      bootstrap/versions.py rebuild-pointer. Apply pending-rollback.
 *   3. An expired probation_deadline is a failure: go to the fallback.
 *   4. The fallback is last_good, or previous_good when version == last_good.
 *      3 starts within 10 minutes (2 on probation) move the pointer to it.
 *   5. A missing versions/<version> uses the fallback.
 *   6. Compare the version with files.tsv. A difference runs the capture
 *      (bootstrap/restore_version.py); a failed capture uses the fallback.
 *   7. Append this start to boot/<version>.attempts.
 *   8. macOS: dlopen the base's libpython in this process, so the signed
 *      helper keeps its privacy grants. Windows and Linux: start the venv's
 *      Python with the same arguments.
 *
 * Compile, macOS:
 *   cc -O2 -I<python include dir> runtime-launcher.c -o OpenProgram
 * Compile, Linux: cc -O2 runtime-launcher.c -o openprogram-launcher
 * Compile, Windows: cl /O2 runtime-launcher.c /link /out:openprogram-launcher.exe
 * -DOPENPROGRAM_LAUNCHER_EXEC makes macOS exec the venv Python as well.
 */
#if defined(_WIN32)
#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <io.h>
#include <process.h>
#if defined(_MSC_VER)
#pragma comment(lib, "shell32")
#endif
#else
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#ifndef _DARWIN_C_SOURCE
#define _DARWIN_C_SOURCE 1
#endif
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#if !defined(OPENPROGRAM_LAUNCHER_EXEC)
#define OPENPROGRAM_EMBED_PYTHON 1
#include <Python.h>
#include <dlfcn.h>
#endif
#endif

#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define OP_PATH 4096
#define OP_FIELDS 64
#define OP_VALUE 256
#define EXIT_USAGE 64
#define EXIT_SETUP 65
#define EXIT_START 127
#define ATTEMPT_WINDOW 600
#define LOCK_BREAK_SECONDS 10
#define LOCK_WAIT_MS 12000

typedef struct {
    char key[OP_VALUE];
    char value[OP_VALUE];
} Field;

typedef struct {
    Field fields[OP_FIELDS];
    int count;
} Pointer;

typedef struct {
    char *path;
    long long size;
    long long mtime_ns;
} Entry;

typedef struct {
    Entry *items;
    size_t count;
} Listing;

static void warn(const char *format, ...) {
    va_list arguments;
    fputs("openprogram launcher: ", stderr);
    va_start(arguments, format);
    vfprintf(stderr, format, arguments);
    va_end(arguments);
    fputc('\n', stderr);
}

static int join(char *out, size_t size, const char *format, ...) {
    va_list arguments;
    int written;
    va_start(arguments, format);
    written = vsnprintf(out, size, format, arguments);
    va_end(arguments);
    return written >= 0 && (size_t)written < size;
}

/*
 * Every Python this starts on Windows runs in UTF-8 mode. Without it, text
 * read or written with no encoding named follows the system code page, so the
 * same code behaves differently on a Chinese and an English Windows. -I makes
 * Python ignore PYTHONUTF8, which is why this is an argument. macOS and Linux
 * already use UTF-8.
 */
#if defined(_WIN32)
#define UTF8_MODE "-X", "utf8",
#define UTF8_MODE_COUNT 2
#else
#define UTF8_MODE
#define UTF8_MODE_COUNT 0
#endif

/* ---- platform layer ---------------------------------------------------- */

#if defined(_WIN32)

static wchar_t *to_wide(const char *path) {
    char normal[OP_PATH + 8];
    size_t length = strlen(path);
    int needed;
    wchar_t *wide;
    if (length >= 240 && path[1] == ':') {
        snprintf(normal, sizeof(normal), "\\\\?\\%s", path);
    } else {
        snprintf(normal, sizeof(normal), "%s", path);
    }
    if (length >= 240) {
        char *cursor;
        for (cursor = normal; *cursor; cursor++) {
            if (*cursor == '/') *cursor = '\\';
        }
    }
    needed = MultiByteToWideChar(CP_UTF8, 0, normal, -1, NULL, 0);
    wide = (wchar_t *)malloc((size_t)needed * sizeof(wchar_t));
    if (wide) MultiByteToWideChar(CP_UTF8, 0, normal, -1, wide, needed);
    return wide;
}

static void to_utf8(const wchar_t *wide, char *out, size_t size) {
    WideCharToMultiByte(CP_UTF8, 0, wide, -1, out, (int)size, NULL, NULL);
}

static void sleep_ms(unsigned int milliseconds) { Sleep(milliseconds); }

static int retriable_share_error(void) {
    DWORD error = GetLastError();
    return error == ERROR_ACCESS_DENIED || error == ERROR_SHARING_VIOLATION;
}

/* Returns 0 when found. is_dir and the times are filled when not NULL. */
static int path_info(const char *path, long long *size, long long *mtime_ns, int *is_dir) {
    WIN32_FILE_ATTRIBUTE_DATA data;
    wchar_t *wide = to_wide(path);
    BOOL found = wide && GetFileAttributesExW(wide, GetFileExInfoStandard, &data);
    free(wide);
    if (!found) return -1;
    if (size) *size = ((long long)data.nFileSizeHigh << 32) | data.nFileSizeLow;
    if (mtime_ns) {
        long long ticks = ((long long)data.ftLastWriteTime.dwHighDateTime << 32)
                          | data.ftLastWriteTime.dwLowDateTime;
        *mtime_ns = (ticks - 116444736000000000LL) * 100;
    }
    if (is_dir) *is_dir = (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    return 0;
}

static char *read_all(const char *path, size_t *length) {
    int attempt;
    HANDLE file = INVALID_HANDLE_VALUE;
    wchar_t *wide = to_wide(path);
    LARGE_INTEGER size;
    char *buffer;
    DWORD got = 0;
    if (!wide) return NULL;
    for (attempt = 0; attempt < 20; attempt++) {
        file = CreateFileW(wide, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (file != INVALID_HANDLE_VALUE || !retriable_share_error()) break;
        Sleep(50);
    }
    free(wide);
    if (file == INVALID_HANDLE_VALUE) return NULL;
    if (!GetFileSizeEx(file, &size) || size.QuadPart > 256 * 1024 * 1024) {
        CloseHandle(file);
        return NULL;
    }
    buffer = (char *)malloc((size_t)size.QuadPart + 1);
    if (buffer && !ReadFile(file, buffer, (DWORD)size.QuadPart, &got, NULL)) {
        free(buffer);
        buffer = NULL;
    }
    CloseHandle(file);
    if (!buffer) return NULL;
    buffer[got] = '\0';
    if (length) *length = got;
    return buffer;
}

static int write_durable_tmp(const char *path, const char *data, size_t length) {
    wchar_t *wide = to_wide(path);
    HANDLE file;
    DWORD wrote = 0;
    int ok;
    if (!wide) return -1;
    file = CreateFileW(wide, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    free(wide);
    if (file == INVALID_HANDLE_VALUE) return -1;
    ok = WriteFile(file, data, (DWORD)length, &wrote, NULL) && wrote == (DWORD)length && FlushFileBuffers(file);
    CloseHandle(file);
    return ok ? 0 : -1;
}

static int replace_file(const char *source, const char *target) {
    wchar_t *from = to_wide(source), *to = to_wide(target);
    int attempt, result = -1;
    for (attempt = 0; from && to && attempt < 20; attempt++) {
        if (MoveFileExW(from, to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
            result = 0;
            break;
        }
        if (!retriable_share_error()) break;
        Sleep(50);
    }
    free(from);
    free(to);
    return result;
}

static void remove_path(const char *path) {
    wchar_t *wide = to_wide(path);
    if (wide) DeleteFileW(wide);
    free(wide);
}

static void make_directory(const char *path) {
    wchar_t *wide = to_wide(path);
    if (wide) CreateDirectoryW(wide, NULL);
    free(wide);
}

static int create_exclusive(const char *path) {
    wchar_t *wide = to_wide(path);
    HANDLE file;
    if (!wide) return -1;
    file = CreateFileW(wide, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    free(wide);
    if (file == INVALID_HANDLE_VALUE) return -1;
    CloseHandle(file);
    return 0;
}

static int append_text(const char *path, const char *text) {
    wchar_t *wide = to_wide(path);
    HANDLE file;
    DWORD wrote = 0;
    int ok;
    if (!wide) return -1;
    file = CreateFileW(wide, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                       OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    free(wide);
    if (file == INVALID_HANDLE_VALUE) return -1;
    ok = WriteFile(file, text, (DWORD)strlen(text), &wrote, NULL);
    CloseHandle(file);
    return ok ? 0 : -1;
}

typedef int (*EntryVisitor)(const char *name, int is_dir, void *context);

static int for_each_entry(const char *path, EntryVisitor visit, void *context) {
    char pattern[OP_PATH];
    wchar_t *wide;
    WIN32_FIND_DATAW item;
    HANDLE search;
    int result = 0;
    if (!join(pattern, sizeof(pattern), "%s\\*", path)) return -1;
    wide = to_wide(pattern);
    if (!wide) return -1;
    search = FindFirstFileW(wide, &item);
    free(wide);
    if (search == INVALID_HANDLE_VALUE) return -1;
    do {
        char name[OP_VALUE * 4];
        to_utf8(item.cFileName, name, sizeof(name));
        if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) continue;
        result = visit(name, (item.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0, context);
    } while (result == 0 && FindNextFileW(search, &item));
    FindClose(search);
    return result;
}

/*
 * Node starts this launcher with handle inheritance on, so every handle the
 * App had marked inheritable arrives here too, the App's DevTools listening
 * socket among them. The launcher waits for the worker for the worker's whole
 * life and passes inheritable handles on to it, so a handle kept here would
 * outlive the App and hold its port shut for the next App. Nothing here uses
 * an inherited handle except the standard ones, so the others are closed
 * before anything starts. ProcessHandleInformation (51) lists this process's
 * handles with their attributes (Windows 8 and later).
 */
typedef struct {
    HANDLE value;
    ULONG_PTR handle_count;
    ULONG_PTR pointer_count;
    ULONG granted_access;
    ULONG object_type_index;
    ULONG attributes;
    ULONG reserved;
} HandleEntry;

typedef struct {
    ULONG_PTR count;
    ULONG_PTR reserved;
    HandleEntry handles[1];
} HandleSnapshot;

typedef LONG (WINAPI *QueryProcess)(HANDLE, ULONG, PVOID, ULONG, PULONG);

#define OP_PROCESS_HANDLE_INFORMATION 51
#define OP_OBJ_INHERIT 0x2
#define OP_INFO_LENGTH_MISMATCH ((LONG)0xC0000004L)

static void close_inherited_handles(void) {
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    QueryProcess query = ntdll
        ? (QueryProcess)(void (*)(void))GetProcAddress(ntdll, "NtQueryInformationProcess")
        : NULL;
    HANDLE keep[3];
    HandleSnapshot *snapshot = NULL;
    ULONG size = 64 * 1024, needed = 0;
    LONG status = OP_INFO_LENGTH_MISMATCH;
    ULONG_PTR index;
    if (!query) return;
    keep[0] = GetStdHandle(STD_INPUT_HANDLE);
    keep[1] = GetStdHandle(STD_OUTPUT_HANDLE);
    keep[2] = GetStdHandle(STD_ERROR_HANDLE);
    while (status == OP_INFO_LENGTH_MISMATCH && size <= 64 * 1024 * 1024) {
        free(snapshot);
        snapshot = (HandleSnapshot *)malloc(size);
        if (!snapshot) return;
        status = query(GetCurrentProcess(), OP_PROCESS_HANDLE_INFORMATION, snapshot, size, &needed);
        size = needed > size ? needed + 4096 : size * 2;
    }
    if (status < 0) {
        free(snapshot);
        return;
    }
    for (index = 0; index < snapshot->count; index++) {
        HANDLE handle = snapshot->handles[index].value;
        if (!(snapshot->handles[index].attributes & OP_OBJ_INHERIT)) continue;
        if (handle == keep[0] || handle == keep[1] || handle == keep[2]) continue;
        CloseHandle(handle);
    }
    free(snapshot);
}

static int spawn_wait(char *const arguments[]) {
    wchar_t command[32768];
    size_t used = 0;
    int index;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD code = EXIT_START;
    DWORD flags = 0;
    command[0] = L'\0';
    for (index = 0; arguments[index]; index++) {
        wchar_t *wide = NULL;
        int needed = MultiByteToWideChar(CP_UTF8, 0, arguments[index], -1, NULL, 0);
        const wchar_t *cursor;
        size_t slashes = 0;
        wide = (wchar_t *)malloc((size_t)needed * sizeof(wchar_t));
        if (!wide) return EXIT_START;
        MultiByteToWideChar(CP_UTF8, 0, arguments[index], -1, wide, needed);
        if (used + (size_t)needed * 2 + 4 >= 32768) {
            free(wide);
            return EXIT_START;
        }
        if (index) command[used++] = L' ';
        command[used++] = L'"';
        for (cursor = wide; *cursor; cursor++) {
            if (*cursor == L'\\') {
                slashes++;
                command[used++] = *cursor;
                continue;
            }
            if (*cursor == L'"') {
                size_t repeat;
                for (repeat = 0; repeat <= slashes; repeat++) command[used++] = L'\\';
            }
            slashes = 0;
            command[used++] = *cursor;
        }
        while (slashes--) command[used++] = L'\\';
        command[used++] = L'"';
        command[used] = L'\0';
        free(wide);
    }
    memset(&startup, 0, sizeof(startup));
    startup.cb = sizeof(startup);
    /*
     * The App starts the worker detached, so this process has no console. A
     * console program started from here would then get a visible console of
     * its own and write there, not to the log this process was handed. Without
     * a console the child gets none either, and the standard handles are
     * passed on by name because they are no longer inherited implicitly.
     */
    if (GetConsoleCP() == 0) {
        flags |= CREATE_NO_WINDOW;
        startup.dwFlags |= STARTF_USESTDHANDLES;
        startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
        startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    }
    if (!CreateProcessW(NULL, command, NULL, NULL, TRUE, flags, NULL, NULL, &startup, &process)) return EXIT_START;
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hProcess);
    CloseHandle(process.hThread);
    return (int)code;
}

static int executable_path(char *out, size_t size) {
    wchar_t wide[32768];
    DWORD length = GetModuleFileNameW(NULL, wide, 32768);
    if (length == 0 || length >= 32768) return -1;
    to_utf8(wide, out, size);
    return 0;
}

/*
 * main() receives the command line in the ANSI code page, which cannot hold
 * every path: a user name outside it arrives as question marks. Everything
 * here is UTF-8, so the arguments are read again from the wide command line.
 */
static char **utf8_arguments(int *count) {
    wchar_t **wide = CommandLineToArgvW(GetCommandLineW(), count);
    char **out;
    int index;
    if (!wide) return NULL;
    out = (char **)calloc((size_t)*count + 1, sizeof(char *));
    for (index = 0; out && index < *count; index++) {
        int needed = WideCharToMultiByte(CP_UTF8, 0, wide[index], -1, NULL, 0, NULL, NULL);
        out[index] = needed > 0 ? (char *)malloc((size_t)needed) : NULL;
        if (!out[index]) {
            out = NULL;
            break;
        }
        WideCharToMultiByte(CP_UTF8, 0, wide[index], -1, out[index], needed, NULL, NULL);
    }
    LocalFree(wide);
    return out;
}

#else /* POSIX */

static void sleep_ms(unsigned int milliseconds) {
    struct timespec pause;
    pause.tv_sec = milliseconds / 1000;
    pause.tv_nsec = (long)(milliseconds % 1000) * 1000000L;
    nanosleep(&pause, NULL);
}

static int path_info(const char *path, long long *size, long long *mtime_ns, int *is_dir) {
    struct stat info;
    if (lstat(path, &info) != 0) return -1;
    if (size) *size = (long long)info.st_size;
    if (mtime_ns) {
#if defined(__APPLE__)
        *mtime_ns = (long long)info.st_mtimespec.tv_sec * 1000000000LL + info.st_mtimespec.tv_nsec;
#else
        *mtime_ns = (long long)info.st_mtim.tv_sec * 1000000000LL + info.st_mtim.tv_nsec;
#endif
    }
    if (is_dir) *is_dir = S_ISDIR(info.st_mode);
    return 0;
}

static char *read_all(const char *path, size_t *length) {
    int descriptor = open(path, O_RDONLY);
    struct stat info;
    char *buffer;
    size_t total = 0;
    if (descriptor < 0) return NULL;
    if (fstat(descriptor, &info) != 0 || info.st_size > 256 * 1024 * 1024) {
        close(descriptor);
        return NULL;
    }
    buffer = (char *)malloc((size_t)info.st_size + 1);
    if (!buffer) {
        close(descriptor);
        return NULL;
    }
    while (total < (size_t)info.st_size) {
        ssize_t got = read(descriptor, buffer + total, (size_t)info.st_size - total);
        if (got < 0) {
            if (errno == EINTR) continue;
            free(buffer);
            close(descriptor);
            return NULL;
        }
        if (got == 0) break;
        total += (size_t)got;
    }
    close(descriptor);
    buffer[total] = '\0';
    if (length) *length = total;
    return buffer;
}

static int write_durable_tmp(const char *path, const char *data, size_t length) {
    int descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    size_t written = 0;
    if (descriptor < 0) return -1;
    while (written < length) {
        ssize_t wrote = write(descriptor, data + written, length - written);
        if (wrote < 0) {
            if (errno == EINTR) continue;
            close(descriptor);
            return -1;
        }
        written += (size_t)wrote;
    }
    if (fsync(descriptor) != 0) {
        close(descriptor);
        return -1;
    }
    return close(descriptor);
}

static int replace_file(const char *source, const char *target) {
    char directory[OP_PATH];
    char *slash;
    int descriptor;
    if (rename(source, target) != 0) return -1;
    if (!join(directory, sizeof(directory), "%s", target)) return -1;
    slash = strrchr(directory, '/');
    if (slash) *slash = '\0';
    descriptor = open(directory, O_RDONLY);
    if (descriptor >= 0) {
        fsync(descriptor);
        close(descriptor);
    }
    return 0;
}

static void remove_path(const char *path) { unlink(path); }

static void make_directory(const char *path) { mkdir(path, 0755); }

static int create_exclusive(const char *path) {
    int descriptor = open(path, O_CREAT | O_EXCL | O_WRONLY, 0600);
    if (descriptor < 0) return -1;
    close(descriptor);
    return 0;
}

static int append_text(const char *path, const char *text) {
    int descriptor = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    ssize_t wrote;
    if (descriptor < 0) return -1;
    wrote = write(descriptor, text, strlen(text));
    close(descriptor);
    return wrote < 0 ? -1 : 0;
}

typedef int (*EntryVisitor)(const char *name, int is_dir, void *context);

static int for_each_entry(const char *path, EntryVisitor visit, void *context) {
    DIR *directory = opendir(path);
    struct dirent *entry;
    int result = 0;
    if (!directory) return -1;
    while (result == 0 && (entry = readdir(directory)) != NULL) {
        char full[OP_PATH];
        int is_dir = 0;
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        if (!join(full, sizeof(full), "%s/%s", path, entry->d_name)) {
            result = -1;
            break;
        }
        if (path_info(full, NULL, NULL, &is_dir) != 0) continue;
        result = visit(entry->d_name, is_dir, context);
    }
    closedir(directory);
    return result;
}

static int spawn_wait(char *const arguments[]) {
    pid_t child = fork();
    int status = 0;
    if (child < 0) return EXIT_START;
    if (child == 0) {
        execv(arguments[0], arguments);
        _exit(EXIT_START);
    }
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return EXIT_START;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : EXIT_START;
}

static int executable_path(char *out, size_t size) {
#if defined(__APPLE__)
    char raw[OP_PATH];
    uint32_t length = sizeof(raw);
    char resolved[PATH_MAX];
    if (_NSGetExecutablePath(raw, &length) != 0 || !realpath(raw, resolved)) return -1;
    return join(out, size, "%s", resolved) ? 0 : -1;
#else
    ssize_t length = readlink("/proc/self/exe", out, size - 1);
    if (length <= 0) return -1;
    out[length] = '\0';
    return 0;
#endif
}

#endif

/* ---- small helpers ----------------------------------------------------- */

static int exists(const char *path) { return path_info(path, NULL, NULL, NULL) == 0; }

static int directory_exists(const char *path) {
    int is_dir = 0;
    return path_info(path, NULL, NULL, &is_dir) == 0 && is_dir;
}

static int current_pid(void) {
#if defined(_WIN32)
    return (int)GetCurrentProcessId();
#else
    return (int)getpid();
#endif
}

static int valid_id(const char *value) {
    const char *cursor;
    if (!value[0] || strlen(value) > 128 || !isalnum((unsigned char)value[0])) return 0;
    for (cursor = value; *cursor; cursor++) {
        if (!isalnum((unsigned char)*cursor) && *cursor != '.' && *cursor != '_' && *cursor != '-') return 0;
    }
    return 1;
}

static int all_digits(const char *value) {
    const char *cursor;
    if (!value[0]) return 0;
    for (cursor = value; *cursor; cursor++) {
        if (!isdigit((unsigned char)*cursor)) return 0;
    }
    return 1;
}

/* ---- pointer ----------------------------------------------------------- */

static const char *get(const Pointer *pointer, const char *key) {
    int index;
    for (index = 0; index < pointer->count; index++) {
        if (strcmp(pointer->fields[index].key, key) == 0) return pointer->fields[index].value;
    }
    return "";
}

static int set(Pointer *pointer, const char *key, const char *value) {
    int index;
    for (index = 0; index < pointer->count; index++) {
        if (strcmp(pointer->fields[index].key, key) == 0) {
            snprintf(pointer->fields[index].value, OP_VALUE, "%s", value);
            return 0;
        }
    }
    if (pointer->count == OP_FIELDS) return -1;
    snprintf(pointer->fields[pointer->count].key, OP_VALUE, "%s", key);
    snprintf(pointer->fields[pointer->count].value, OP_VALUE, "%s", value);
    pointer->count++;
    return 0;
}

static int parse_pointer(const char *text, Pointer *pointer) {
    const char *cursor = text;
    pointer->count = 0;
    while (*cursor) {
        const char *end = strchr(cursor, '\n');
        size_t length = end ? (size_t)(end - cursor) : strlen(cursor);
        const char *equals = memchr(cursor, '=', length);
        Field *field;
        size_t key_length, value_length;
        const char *probe;
        if (!equals || equals == cursor || pointer->count == OP_FIELDS) return -1;
        for (probe = cursor; probe < cursor + length; probe++) {
            if ((unsigned char)*probe > 127 || *probe == '\r') return -1;
        }
        key_length = (size_t)(equals - cursor);
        value_length = length - key_length - 1;
        if (key_length >= OP_VALUE || value_length >= OP_VALUE) return -1;
        field = &pointer->fields[pointer->count++];
        memcpy(field->key, cursor, key_length);
        field->key[key_length] = '\0';
        memcpy(field->value, equals + 1, value_length);
        field->value[value_length] = '\0';
        cursor += length + (end ? 1 : 0);
    }
    if (strcmp(get(pointer, "schema"), "1") != 0 || !all_digits(get(pointer, "generation"))
        || !valid_id(get(pointer, "version")) || !valid_id(get(pointer, "base"))) {
        return -1;
    }
    return 0;
}

static int load_pointer_file(const char *path, Pointer *pointer) {
    char *text = read_all(path, NULL);
    int result;
    if (!text) return -1;
    result = parse_pointer(text, pointer);
    free(text);
    return result;
}

static size_t render_pointer(const Pointer *pointer, char *out, size_t size) {
    size_t used = 0;
    int index;
    for (index = 0; index < pointer->count; index++) {
        int written = snprintf(out + used, size - used, "%s=%s\n",
                               pointer->fields[index].key, pointer->fields[index].value);
        if (written < 0 || (size_t)written >= size - used) return 0;
        used += (size_t)written;
    }
    return used;
}

static int lock_pointer(const char *root) {
    char path[OP_PATH];
    int waited = 0;
    if (!join(path, sizeof(path), "%s/current.lock", root)) return -1;
    while (create_exclusive(path) != 0) {
        long long mtime_ns = 0;
        if (path_info(path, NULL, &mtime_ns, NULL) == 0
            && (long long)time(NULL) - mtime_ns / 1000000000LL > LOCK_BREAK_SECONDS) {
            remove_path(path);
            continue;
        }
        if (waited >= LOCK_WAIT_MS) return -1;
        sleep_ms(50);
        waited += 50;
    }
    return 0;
}

static void unlock_pointer(const char *root) {
    char path[OP_PATH];
    if (join(path, sizeof(path), "%s/current.lock", root)) remove_path(path);
}

static int write_durably(const char *path, const char *data, size_t length) {
    char temporary[OP_PATH];
    if (!join(temporary, sizeof(temporary), "%s.%d.tmp", path, current_pid())) return -1;
    if (write_durable_tmp(temporary, data, length) != 0 || replace_file(temporary, path) != 0) {
        remove_path(temporary);
        return -1;
    }
    return 0;
}

/*
 * The procedure every writer follows: lock, compare generation, write the
 * next generation and a copy of the old pointer durably, rename, unlock.
 * Returns 0 on success, 1 on a generation conflict, -1 on an error.
 */
static int compare_and_set(const char *root, Pointer *next, long long expected) {
    char path[OP_PATH], previous_path[OP_PATH], rendered[OP_FIELDS * OP_VALUE * 2], generation[32];
    Pointer present;
    char *old;
    size_t old_length = 0, length;
    int result = -1;
    if (!join(path, sizeof(path), "%s/current", root)
        || !join(previous_path, sizeof(previous_path), "%s/current.prev", root)) return -1;
    if (lock_pointer(root) != 0) return -1;
    if (load_pointer_file(path, &present) != 0) goto done;
    if (strtoll(get(&present, "generation"), NULL, 10) != expected) {
        result = 1;
        goto done;
    }
    snprintf(generation, sizeof(generation), "%lld", expected + 1);
    set(next, "generation", generation);
    set(next, "writer", "launcher");
    length = render_pointer(next, rendered, sizeof(rendered));
    if (length == 0) goto done;
    old = read_all(path, &old_length);
    if (!old) goto done;
    if (write_durably(previous_path, old, old_length) == 0
        && write_durably(path, rendered, length) == 0) {
        result = 0;
    }
    free(old);
done:
    unlock_pointer(root);
    return result;
}

static void log_recovery(const char *root, const char *from, const char *to, const char *reason) {
    char path[OP_PATH], line[OP_VALUE * 3 + 128], stamp[32];
    time_t now = time(NULL);
    struct tm *moment = gmtime(&now);
    if (!moment || !join(path, sizeof(path), "%s/recovery.log", root)) return;
    strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%SZ", moment);
    snprintf(line, sizeof(line), "%s launcher: moved from %s to %s: %s\n", stamp, from, to, reason);
    append_text(path, line);
}

static const char *fallback_field(const Pointer *pointer, int base) {
    const char *version = get(pointer, "version"), *last_good = get(pointer, "last_good");
    if (last_good[0] && strcmp(version, last_good) == 0) {
        return get(pointer, base ? "previous_good_base" : "previous_good");
    }
    return get(pointer, base ? "last_good_base" : "last_good");
}

static int has_fallback(const char *root, const Pointer *pointer) {
    char path[OP_PATH];
    const char *version = fallback_field(pointer, 0), *base = fallback_field(pointer, 1);
    if (!valid_id(version) || !valid_id(base)) return 0;
    return join(path, sizeof(path), "%s/versions/%s", root, version) && directory_exists(path);
}

/* Returns 0 when the pointer moved or moved under us; `pointer` is current. */
static int move_to_fallback(const char *root, Pointer *pointer, const char *reason) {
    char from[OP_VALUE], to[OP_VALUE], base[OP_VALUE], path[OP_PATH];
    long long generation = strtoll(get(pointer, "generation"), NULL, 10);
    int result;
    Pointer next = *pointer;
    snprintf(from, sizeof(from), "%s", get(pointer, "version"));
    snprintf(to, sizeof(to), "%s", fallback_field(pointer, 0));
    snprintf(base, sizeof(base), "%s", fallback_field(pointer, 1));
    set(&next, "version", to);
    set(&next, "base", base);
    set(&next, "probation", "");
    set(&next, "probation_deadline", "");
    result = compare_and_set(root, &next, generation);
    if (result < 0) return -1;
    if (!join(path, sizeof(path), "%s/current", root) || load_pointer_file(path, pointer) != 0) return -1;
    if (result == 0) log_recovery(root, from, to, reason);
    return 0;
}

/* ---- starting Python tools --------------------------------------------- */

static int base_python_path(const char *root, const char *base, char *out, size_t size) {
#if defined(_WIN32)
    return join(out, size, "%s/bases/%s/python/python.exe", root, base);
#else
    return join(out, size, "%s/bases/%s/python/bin/python3", root, base);
#endif
}

typedef struct {
    const char *root;
    char *found;
    size_t size;
} BaseSearch;

static int find_base_visitor(const char *name, int is_dir, void *context) {
    BaseSearch *search = (BaseSearch *)context;
    if (!is_dir || name[0] == '.') return 0;
    if (base_python_path(search->root, name, search->found, search->size) && exists(search->found)) return 1;
    search->found[0] = '\0';
    return 0;
}

static int find_any_base(const char *root, char *out, size_t size) {
    char bases[OP_PATH];
    BaseSearch search;
    search.root = root;
    search.found = out;
    search.size = size;
    out[0] = '\0';
    if (!join(bases, sizeof(bases), "%s/bases", root)) return -1;
    for_each_entry(bases, find_base_visitor, &search);
    return out[0] ? 0 : -1;
}

/* ---- pointer loading --------------------------------------------------- */

static int load_or_repair_pointer(const char *root, Pointer *pointer) {
    char path[OP_PATH], previous_path[OP_PATH], python[OP_PATH], tool[OP_PATH], rendered[OP_FIELDS * OP_VALUE * 2];
    Pointer previous;
    if (!join(path, sizeof(path), "%s/current", root)
        || !join(previous_path, sizeof(previous_path), "%s/current.prev", root)) return -1;
    if (load_pointer_file(path, pointer) == 0) return 0;
    if (load_pointer_file(previous_path, &previous) == 0) {
        size_t length = render_pointer(&previous, rendered, sizeof(rendered));
        int written;
        if (length && lock_pointer(root) == 0) {
            written = write_durably(path, rendered, length);
            unlock_pointer(root);
            if (written == 0 && load_pointer_file(path, pointer) == 0) {
                return 0;
            }
        }
    }
    if (find_any_base(root, python, sizeof(python)) == 0
        && join(tool, sizeof(tool), "%s/bootstrap/versions.py", root) && exists(tool)) {
        char *arguments[] = {python, "-I", "-B", UTF8_MODE tool, "--root", (char *)root, "rebuild-pointer", NULL};
        if (spawn_wait(arguments) == 0 && load_pointer_file(path, pointer) == 0) return 0;
    }
    return -1;
}

static void apply_pending_rollback(const char *root, Pointer *pointer) {
    char path[OP_PATH];
    char *text;
    const char *value;
    char *end;
    if (!join(path, sizeof(path), "%s/pending-rollback", root)) return;
    text = read_all(path, NULL);
    if (!text) return;
    value = strncmp(text, "version=", 8) == 0 ? text + 8 : NULL;
    end = text + strcspn(text, "\r\n");
    *end = '\0';
    if (value && strcmp(value, get(pointer, "version")) == 0) {
        if (has_fallback(root, pointer)) {
            if (move_to_fallback(root, pointer, "pending rollback") == 0) remove_path(path);
        }
    } else {
        remove_path(path);  /* Already away from that version. */
    }
    free(text);
}

/* ---- the seal check ---------------------------------------------------- */

static int compare_entries(const void *left, const void *right) {
    return strcmp(((const Entry *)left)->path, ((const Entry *)right)->path);
}

static void free_listing(Listing *listing, char *text) {
    free(listing->items);
    free(text);
}

static char *load_listing(const char *path, Listing *listing) {
    char *text = read_all(path, NULL);
    char *cursor;
    size_t capacity = 1024;
    listing->count = 0;
    listing->items = NULL;
    if (!text) return NULL;
    listing->items = (Entry *)malloc(capacity * sizeof(Entry));
    for (cursor = text; listing->items && *cursor;) {
        char *end = strchr(cursor, '\n');
        char *first = strchr(cursor, '\t');
        char *second = first ? strchr(first + 1, '\t') : NULL;
        if (!end || !first || !second || second > end) {
            free(listing->items);
            free(text);
            listing->items = NULL;
            return NULL;
        }
        *end = '\0';
        *first = '\0';
        *second = '\0';
        if (listing->count == capacity) {
            Entry *grown;
            capacity *= 2;
            grown = (Entry *)realloc(listing->items, capacity * sizeof(Entry));
            if (!grown) {
                free(listing->items);
                free(text);
                listing->items = NULL;
                return NULL;
            }
            listing->items = grown;
        }
        listing->items[listing->count].path = cursor;
        listing->items[listing->count].size = strtoll(first + 1, NULL, 10);
        listing->items[listing->count].mtime_ns = strtoll(second + 1, NULL, 10);
        listing->count++;
        cursor = end + 1;
    }
    if (!listing->items) {
        free(text);
        return NULL;
    }
    qsort(listing->items, listing->count, sizeof(Entry), compare_entries);
    return text;
}

static int listed(const Listing *listing, const char *relative) {
    Entry key;
    key.path = (char *)relative;
    return bsearch(&key, listing->items, listing->count, sizeof(Entry), compare_entries) != NULL;
}

typedef struct {
    const char *version_dir;
    const char *relative;
    const Listing *listing;
    char *why;
    size_t why_size;
} WalkContext;

static int walk_tree(const char *version_dir, const char *relative, const Listing *listing, char *why, size_t why_size);

static int walk_visitor(const char *name, int is_dir, void *raw) {
    WalkContext *context = (WalkContext *)raw;
    char child[OP_PATH];
    if (!join(child, sizeof(child), "%s/%s", context->relative, name)) return 1;
    if (is_dir) {
        if (strcmp(name, "__pycache__") == 0) return 0;
        return walk_tree(context->version_dir, child, context->listing, context->why, context->why_size);
    }
    if (!listed(context->listing, child)) {
        snprintf(context->why, context->why_size, "unlisted file %s", child);
        return 1;
    }
    return 0;
}

static int walk_tree(const char *version_dir, const char *relative, const Listing *listing, char *why, size_t why_size) {
    char path[OP_PATH];
    WalkContext context;
    if (!join(path, sizeof(path), "%s/%s", version_dir, relative)) return 1;
    context.version_dir = version_dir;
    context.relative = relative;
    context.listing = listing;
    context.why = why;
    context.why_size = why_size;
    return for_each_entry(path, walk_visitor, &context) > 0 ? 1 : 0;
}

/* 0 when the version matches its seal. */
static int check_seal(const char *version_dir, const char *base, char *why, size_t why_size) {
    char path[OP_PATH], site[OP_PATH], minor[32];
    Listing listing;
    char *text;
    size_t index;
    int result = 0;
    const char *dash = strchr(base, '-');
    if (!dash || strncmp(base, "py", 2) != 0 || (size_t)(dash - base - 2) >= sizeof(minor)) {
        snprintf(why, why_size, "unreadable base id %s", base);
        return 1;
    }
    memcpy(minor, base + 2, (size_t)(dash - base - 2));
    minor[dash - base - 2] = '\0';
    if (!join(path, sizeof(path), "%s/files.tsv", version_dir)) return 1;
    text = load_listing(path, &listing);
    if (!text) {
        snprintf(why, why_size, "unreadable files.tsv");
        return 1;
    }
    for (index = 0; index < listing.count; index++) {
        long long size = 0, mtime_ns = 0;
        if (!join(path, sizeof(path), "%s/%s", version_dir, listing.items[index].path)
            || path_info(path, &size, &mtime_ns, NULL) != 0) {
            snprintf(why, why_size, "missing file %s", listing.items[index].path);
            result = 1;
            break;
        }
        if (size != listing.items[index].size || mtime_ns != listing.items[index].mtime_ns) {
            snprintf(why, why_size, "changed file %s", listing.items[index].path);
            result = 1;
            break;
        }
    }
#if defined(_WIN32)
    join(site, sizeof(site), "venv/Lib/site-packages/openprogram");
    (void)minor;
#else
    join(site, sizeof(site), "venv/lib/python%s/site-packages/openprogram", minor);
#endif
    if (result == 0) result = walk_tree(version_dir, site, &listing, why, why_size);
    if (result == 0) result = walk_tree(version_dir, "desktop", &listing, why, why_size);
    free_listing(&listing, text);
    return result;
}

static int run_capture(const char *root, const char *version, const char *base) {
    char python[OP_PATH], tool[OP_PATH];
    if (!base_python_path(root, base, python, sizeof(python))
        || !join(tool, sizeof(tool), "%s/bootstrap/restore_version.py", root)
        || !exists(python) || !exists(tool)) return -1;
    {
        char *arguments[] = {python, "-I", "-B", UTF8_MODE tool, "--root", (char *)root, (char *)version, NULL};
        return spawn_wait(arguments) == 0 ? 0 : -1;
    }
}

/* ---- boot records ------------------------------------------------------ */

static int count_recent_attempts(const char *root, const char *version) {
    char path[OP_PATH];
    char *text, *cursor;
    long long now = (long long)time(NULL);
    int count = 0;
    if (!join(path, sizeof(path), "%s/boot/%s.attempts", root, version)) return 0;
    text = read_all(path, NULL);
    if (!text) return 0;
    for (cursor = text; *cursor;) {
        char *end = NULL;
        long long moment = strtoll(cursor, &end, 10);
        if (end == cursor) break;
        if (now - moment < ATTEMPT_WINDOW) count++;
        cursor = end;
        while (*cursor == '\n' || *cursor == '\r' || *cursor == ' ') cursor++;
    }
    free(text);
    return count;
}

static void record_attempt(const char *root, const char *version) {
    char directory[OP_PATH], path[OP_PATH], line[32];
    if (!join(directory, sizeof(directory), "%s/boot", root)
        || !join(path, sizeof(path), "%s/boot/%s.attempts", root, version)) return;
    make_directory(directory);
    snprintf(line, sizeof(line), "%lld\n", (long long)time(NULL));
    append_text(path, line);
}

/* ---- developer mode ---------------------------------------------------- */

static int json_string(const char *json, const char *key, char *out, size_t size) {
    char quoted[128];
    const char *cursor;
    size_t used = 0;
    snprintf(quoted, sizeof(quoted), "\"%s\"", key);
    cursor = strstr(json, quoted);
    if (!cursor) return -1;
    cursor += strlen(quoted);
    while (*cursor == ' ' || *cursor == ':' || *cursor == '\t' || *cursor == '\n' || *cursor == '\r') cursor++;
    if (*cursor != '"') return -1;
    cursor++;
    while (*cursor && *cursor != '"') {
        char character = *cursor;
        if (character == '\\' && cursor[1]) {
            cursor++;
            character = *cursor == 'n' ? '\n' : *cursor == 't' ? '\t' : *cursor;
        }
        if (used + 1 >= size) return -1;
        out[used++] = character;
        cursor++;
    }
    out[used] = '\0';
    return *cursor == '"' ? 0 : -1;
}

static void set_python_path(const char *checkout) {
#if defined(_WIN32)
    wchar_t *wide = to_wide(checkout);
    if (wide) SetEnvironmentVariableW(L"PYTHONPATH", wide);
    free(wide);
#else
    setenv("PYTHONPATH", checkout, 1);
#endif
}

static int run_process(char **arguments) {
#if defined(_WIN32)
    return spawn_wait(arguments);
#else
    execv(arguments[0], arguments);
    warn("cannot start %s: %s", arguments[0], strerror(errno));
    return EXIT_START;
#endif
}

static int follow_developer(const char *path, int argc, char **argv) {
    char checkout[OP_PATH], python[OP_PATH];
    char *json = read_all(path, NULL);
    char **arguments;
    int index;
    if (!json || json_string(json, "checkout", checkout, sizeof(checkout)) != 0
        || json_string(json, "python", python, sizeof(python)) != 0) {
        warn("developer.json is unreadable: %s", path);
        free(json);
        return EXIT_SETUP;
    }
    free(json);
    set_python_path(checkout);
    {
        char *options[] = {UTF8_MODE NULL};
        arguments = (char **)calloc((size_t)argc + 2 + UTF8_MODE_COUNT, sizeof(char *));
        if (!arguments) return EXIT_START;
        arguments[0] = python;
        for (index = 0; index < UTF8_MODE_COUNT; index++) arguments[index + 1] = options[index];
        for (index = 0; index < argc; index++) arguments[index + 1 + UTF8_MODE_COUNT] = argv[index];
    }
    return run_process(arguments);
}

/* ---- starting the version ---------------------------------------------- */

#if defined(OPENPROGRAM_EMBED_PYTHON)

typedef void (*ConfigInit)(PyConfig *);
typedef PyStatus (*ConfigSetBytes)(PyConfig *, wchar_t **, const char *);
typedef PyStatus (*ConfigSetArgv)(PyConfig *, Py_ssize_t, char *const *);
typedef PyStatus (*InitializeFromConfig)(const PyConfig *);
typedef void (*ConfigClear)(PyConfig *);
typedef int (*RunMain)(void);
typedef int (*StatusIsException)(PyStatus);
typedef void (*ExitStatusException)(PyStatus);

/*
 * Run Python inside this process, so macOS attributes privacy grants to the
 * one signed helper whatever version is running. PyConfig is ABI specific,
 * which is why the launcher is compiled per Python minor.
 */
static int run_embedded(const char *root, const char *base, const char *venv_python, int argc, char **argv) {
    char library[OP_PATH];
    void *handle;
    ConfigInit init;
    ConfigSetBytes set_bytes;
    ConfigSetArgv set_argv;
    InitializeFromConfig initialize;
    ConfigClear clear;
    RunMain run_main;
    StatusIsException is_exception;
    ExitStatusException exit_status;
    PyConfig config;
    PyStatus status;
    if (!join(library, sizeof(library), "%s/bases/%s/python/lib/libpython%d.%d.dylib",
              root, base, PY_MAJOR_VERSION, PY_MINOR_VERSION)) return EXIT_START;
    handle = dlopen(library, RTLD_NOW | RTLD_GLOBAL);
    if (!handle) {
        warn("cannot load %s: %s", library, dlerror());
        return EXIT_START;
    }
    init = (ConfigInit)dlsym(handle, "PyConfig_InitPythonConfig");
    set_bytes = (ConfigSetBytes)dlsym(handle, "PyConfig_SetBytesString");
    set_argv = (ConfigSetArgv)dlsym(handle, "PyConfig_SetBytesArgv");
    initialize = (InitializeFromConfig)dlsym(handle, "Py_InitializeFromConfig");
    clear = (ConfigClear)dlsym(handle, "PyConfig_Clear");
    run_main = (RunMain)dlsym(handle, "Py_RunMain");
    is_exception = (StatusIsException)dlsym(handle, "PyStatus_Exception");
    exit_status = (ExitStatusException)dlsym(handle, "Py_ExitStatusException");
    if (!init || !set_bytes || !set_argv || !initialize || !clear || !run_main || !is_exception || !exit_status) {
        warn("%s is missing the embedding interface", library);
        return EXIT_START;
    }
    init(&config);
    config.parse_argv = 1;
    status = set_bytes(&config, &config.program_name, venv_python);
    if (!is_exception(status)) status = set_bytes(&config, &config.executable, venv_python);
    if (!is_exception(status)) status = set_argv(&config, (Py_ssize_t)argc, argv);
    if (!is_exception(status)) status = initialize(&config);
    clear(&config);
    if (is_exception(status)) exit_status(status);
    return run_main();
}

#endif

static int start_version(const char *root, const char *version, const char *base, int argc, char **argv) {
    char venv_python[OP_PATH];
    char **arguments;
    int index;
#if defined(_WIN32)
    if (!join(venv_python, sizeof(venv_python), "%s/versions/%s/venv/Scripts/python.exe", root, version)) return EXIT_START;
#else
    if (!join(venv_python, sizeof(venv_python), "%s/versions/%s/venv/bin/python", root, version)) return EXIT_START;
#endif
    {
        char *options[] = {"-I", "-B", UTF8_MODE NULL};
        int count = 2 + UTF8_MODE_COUNT;
        arguments = (char **)calloc((size_t)argc + (size_t)count + 2, sizeof(char *));
        if (!arguments) return EXIT_START;
        arguments[0] = venv_python;
        for (index = 0; index < count; index++) arguments[index + 1] = options[index];
        for (index = 0; index < argc; index++) arguments[index + 1 + count] = argv[index];
        argc += 1 + count;
    }
#if defined(OPENPROGRAM_EMBED_PYTHON)
    return run_embedded(root, base, venv_python, argc, arguments);
#else
    (void)base;
    return run_process(arguments);
#endif
}

/* ---- root ------------------------------------------------------------- */

static int root_from_executable(char *out, size_t size) {
    char executable[OP_PATH];
    char *cursor;
    if (executable_path(executable, sizeof(executable)) != 0) return -1;
#if defined(_WIN32)
    for (cursor = executable; *cursor; cursor++) {
        if (*cursor == '\\') *cursor = '/';
    }
#endif
    cursor = executable + strlen(executable);
    while (cursor > executable) {
        char *slash;
        *cursor = '\0';
        slash = strrchr(executable, '/');
        if (!slash) return -1;
        if (strcmp(slash + 1, "identity") == 0) {
            *slash = '\0';
            return join(out, size, "%s", executable) ? 0 : -1;
        }
        cursor = slash;
    }
    return -1;
}

int main(int argc, char **argv) {
    char root[OP_PATH], path[OP_PATH], why[OP_PATH];
    int rest = 1, round, guard_moved = 0;
    Pointer pointer;
#if defined(_WIN32)
    int wide_count = 0;
    char **wide_arguments = utf8_arguments(&wide_count);
    if (!wide_arguments) {
        warn("the command line cannot be read");
        return EXIT_START;
    }
    argc = wide_count;
    argv = wide_arguments;
    close_inherited_handles();
#endif
    if (argc >= 3 && strcmp(argv[1], "--root") == 0) {
        if (!join(root, sizeof(root), "%s", argv[2])) return EXIT_USAGE;
        rest = 3;
    } else if (root_from_executable(root, sizeof(root)) != 0) {
        warn("this launcher is not inside an identity directory; pass --root");
        return EXIT_USAGE;
    }

    if (join(path, sizeof(path), "%s/developer.json", root) && exists(path)) {
        return follow_developer(path, argc - rest, argv + rest);
    }
    if (join(path, sizeof(path), "%s/.seeding", root) && exists(path)) {
        warn("first-run setup has not finished: %s", root);
        return EXIT_SETUP;
    }
    if (load_or_repair_pointer(root, &pointer) != 0) {
        warn("no usable pointer in %s", root);
        return EXIT_SETUP;
    }
    apply_pending_rollback(root, &pointer);

    for (round = 0; round < 4; round++) {
        char version[OP_VALUE], base[OP_VALUE], version_dir[OP_PATH];
        const char *failure = NULL;
        int on_probation, limit;
        snprintf(version, sizeof(version), "%s", get(&pointer, "version"));
        snprintf(base, sizeof(base), "%s", get(&pointer, "base"));
        on_probation = get(&pointer, "probation")[0] != '\0';
        limit = on_probation ? 2 : 3;
        if (!join(version_dir, sizeof(version_dir), "%s/versions/%s", root, version)) return EXIT_START;

        if (!guard_moved) {
            const char *deadline = get(&pointer, "probation_deadline");
            if (on_probation && all_digits(deadline) && strtoll(deadline, NULL, 10) < (long long)time(NULL)) {
                failure = "probation deadline passed";
            } else if (count_recent_attempts(root, version) >= limit) {
                failure = on_probation ? "repeated abnormal ends on probation" : "repeated abnormal ends";
            }
        }
        if (!failure && !directory_exists(version_dir)) failure = "version directory is missing";
        if (!failure && check_seal(version_dir, base, why, sizeof(why)) != 0) {
            warn("%s differs from its seal (%s); capturing", version, why);
            if (run_capture(root, version, base) != 0
                || check_seal(version_dir, base, why, sizeof(why)) != 0) {
                record_attempt(root, version);
                failure = "direct-edit capture failed";
            }
        }
        if (!failure) {
            record_attempt(root, version);
            return start_version(root, version, base, argc - rest, argv + rest);
        }
        if (!has_fallback(root, &pointer)) {
            if (directory_exists(version_dir) && strcmp(failure, "direct-edit capture failed") != 0) {
                /* Nowhere to return to: starting it is the only way forward. */
                record_attempt(root, version);
                return start_version(root, version, base, argc - rest, argv + rest);
            }
            warn("%s cannot start (%s) and no fallback version exists", version, failure);
            return EXIT_START;
        }
        warn("%s: %s; returning to %s", version, failure, fallback_field(&pointer, 0));
        if (move_to_fallback(root, &pointer, failure) != 0) {
            warn("could not move the pointer");
            return EXIT_START;
        }
        guard_moved = 1;
    }
    warn("no version could be started");
    return EXIT_START;
}
