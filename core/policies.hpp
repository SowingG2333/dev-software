// Internal options protocol: one quoted UTF-8 value per line. No shell parsing.
#include <cstdlib>
#include <codecvt>
#include <locale>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <aclapi.h>
#include <sddl.h>
#pragma comment(lib, "advapi32.lib")
#else
#include <sys/stat.h>
#include <fcntl.h>
#endif

static fs::path environmentPath(const char* key) {
#ifdef _WIN32
    std::wstring wide(key, key + std::char_traits<char>::length(key));
    const auto value = _wgetenv(wide.c_str());
    return value ? fs::path(value) : fs::path();
#else
    const auto value = std::getenv(key);
    return value ? fs::path(value) : fs::path();
#endif
}
static fs::path decision;
static bool committed = false;
static void cancelled() {
    require(committed || decision.empty() || !fs::exists(decision), "Operation cancelled before commit.");
}
static void commit() {
    cancelled();
    if (!committed && !decision.empty()) require(fs::create_directory(decision), "Operation cancelled before commit.");
    committed = true;
}
struct Policies {
    std::vector<std::string> includes, excludes, names, types, owners;
    std::uint64_t minBytes = 0, maxBytes = UINT64_MAX, maxOutput = 8ULL * 1024 * 1024 * 1024;
    std::int64_t after = INT64_MIN, before = INT64_MAX;
    bool archive = false, compress = false, times = false, permissions = false;
} policy;
static void loadPolicies() {
    decision = environmentPath("BACKNEST_DECISION");
    const auto path = environmentPath("BACKNEST_OPTIONS");
    if (path.empty()) return;
    require(fs::file_size(path) <= 65536, "Options file too large.");
    std::ifstream input(path, std::ios::binary);
    std::string key, value;
    while (input >> key >> std::quoted(value)) {
        if (key == "include") policy.includes.push_back(value);
        else if (key == "exclude") policy.excludes.push_back(value);
        else if (key == "name") policy.names.push_back(value);
        else if (key == "type") policy.types.push_back(value);
        else if (key == "owner") policy.owners.push_back(value);
        else if (key == "min") policy.minBytes = std::stoull(value);
        else if (key == "max") policy.maxBytes = std::stoull(value);
        else if (key == "limit") policy.maxOutput = std::stoull(value);
        else if (key == "after") policy.after = std::stoll(value);
        else if (key == "before") policy.before = std::stoll(value);
        else if (key == "archive") policy.archive = value == "1";
        else if (key == "compress") policy.compress = value == "1";
        else if (key == "times") policy.times = value == "1";
        else if (key == "permissions") policy.permissions = value == "1";
        else throw std::runtime_error("Unknown native option: " + key);
    }
    require(input.eof(), "Malformed native options.");
    require(policy.minBytes <= policy.maxBytes && policy.after <= policy.before, "Reversed filter range.");
}
// Greedy wildcard matcher over Unicode code points: * includes '/', ? is one code point.
static bool wildcard(const std::string& pattern, const std::string& value) {
    std::wstring_convert<std::codecvt_utf8<char32_t>, char32_t> converter;
    const auto p = converter.from_bytes(pattern), v = converter.from_bytes(value);
    std::size_t i = 0, j = 0, star = std::u32string::npos, retry = 0;
    while (j < v.size()) {
        if (i < p.size() && (p[i] == U'?' || p[i] == v[j])) { ++i; ++j; }
        else if (i < p.size() && p[i] == U'*') { star = i++; retry = j; }
        else if (star != std::u32string::npos) { i = star + 1; j = ++retry; }
        else return false;
    }
    while (i < p.size() && p[i] == U'*') ++i;
    return i == p.size();
}
static bool matches(const std::vector<std::string>& patterns, const std::string& value) {
    return patterns.empty() || std::any_of(patterns.begin(), patterns.end(), [&](const auto& p) { return wildcard(p, value); });
}
static std::string ownerId(const fs::path& path) {
#ifdef _WIN32
    PSID owner = nullptr; PSECURITY_DESCRIPTOR descriptor = nullptr;
    require(GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                 &owner, nullptr, nullptr, nullptr, &descriptor) == ERROR_SUCCESS, "Cannot read owner SID.");
    LPSTR sid = nullptr;
    const bool ok = ConvertSidToStringSidA(owner, &sid) != 0;
    const std::string result = ok ? sid : "";
    if (sid) LocalFree(sid);
    LocalFree(descriptor);
    require(ok, "Cannot convert owner SID.");
    return result;
#else
    struct stat info{};
    require(lstat(path.c_str(), &info) == 0, "Cannot read owner uid.");
    return std::to_string(info.st_uid);
#endif
}
static void supportedType(const fs::path& path) {
#ifdef _WIN32
    const auto attributes = GetFileAttributesW(path.c_str());
    require(attributes != INVALID_FILE_ATTRIBUTES, "Cannot read file attributes.");
    require(!(attributes & FILE_ATTRIBUTE_REPARSE_POINT), "Unsupported reparse point: " + text(path));
#endif
}
static std::int64_t modificationNs(const fs::path& path) {
#ifdef _WIN32
    WIN32_FILE_ATTRIBUTE_DATA info{};
    require(GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &info), "Cannot read modification time.");
    ULARGE_INTEGER ticks; ticks.LowPart = info.ftLastWriteTime.dwLowDateTime; ticks.HighPart = info.ftLastWriteTime.dwHighDateTime;
    const auto unixTicks = static_cast<std::int64_t>(ticks.QuadPart) - 116444736000000000LL;
    require(unixTicks >= INT64_MIN / 100 && unixTicks <= INT64_MAX / 100, "Modification time outside supported range.");
    return unixTicks * 100;
#else
    struct stat info{}; require(lstat(path.c_str(), &info) == 0, "Cannot read modification time.");
#ifdef __APPLE__
    const auto stamp = info.st_mtimespec;
#else
    const auto stamp = info.st_mtim;
#endif
    require(stamp.tv_sec >= INT64_MIN / 1000000000LL && stamp.tv_sec < INT64_MAX / 1000000000LL, "Modification time outside supported range.");
    return static_cast<std::int64_t>(stamp.tv_sec) * 1000000000LL + stamp.tv_nsec;
#endif
}
static void setModificationNs(const fs::path& path, std::int64_t value) {
#ifdef _WIN32
    const HANDLE handle = CreateFileW(path.c_str(), FILE_WRITE_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                      nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    require(handle != INVALID_HANDLE_VALUE, "Cannot open file to restore timestamp.");
    ULARGE_INTEGER ticks; ticks.QuadPart = static_cast<ULONGLONG>(value / 100 + 116444736000000000LL);
    FILETIME stamp{ticks.LowPart, ticks.HighPart}; const bool ok = SetFileTime(handle, nullptr, nullptr, &stamp) != 0;
    CloseHandle(handle); require(ok, "Cannot restore modification time.");
#else
    timespec stamps[2]{}; stamps[0].tv_nsec = UTIME_OMIT;
    stamps[1].tv_sec = value / 1000000000LL; stamps[1].tv_nsec = value % 1000000000LL;
    if (stamps[1].tv_nsec < 0) { --stamps[1].tv_sec; stamps[1].tv_nsec += 1000000000LL; }
    require(utimensat(AT_FDCWD, path.c_str(), stamps, 0) == 0, "Cannot restore modification time.");
#endif
}
static bool selectedEntry(const fs::path& source, const Entry& entry) {
    const auto name = text(entry.relative);
    if (!policy.excludes.empty()) {
        for (auto p = entry.relative; !p.empty(); p = p.parent_path())
            if (matches(policy.excludes, text(p))) return false;
    }
    if (!matches(policy.includes, name) || !matches(policy.names, text(entry.relative.filename()))) return false;
    if (!policy.types.empty() && std::find(policy.types.begin(), policy.types.end(), entry.directory ? "directory" : "regular") == policy.types.end()) return false;
    if (entry.bytes < policy.minBytes || entry.bytes > policy.maxBytes) return false;
    const auto stamp = entry.modifiedNs / 1000000000LL;
    if (stamp < policy.after || stamp > policy.before) return false;
    if (!policy.owners.empty()) {
        const auto owner = ownerId(source / entry.relative);
        if (std::find(policy.owners.begin(), policy.owners.end(), owner) == policy.owners.end()) return false;
    }
    return true;
}
static void filterManifest(const fs::path& source, Manifest& manifest) {
    std::set<std::string> keep;
    for (const auto& e : manifest.entries) if (selectedEntry(source, e))
        for (auto path = e.relative; !path.empty(); path = path.parent_path()) keep.insert(text(path));
    manifest.entries.erase(std::remove_if(manifest.entries.begin(), manifest.entries.end(),
        [&](const auto& e) { return !keep.count(text(e.relative)); }), manifest.entries.end());
    manifest.bytes = manifest.files = manifest.directories = 0;
    for (const auto& e : manifest.entries) {
        if (e.directory) ++manifest.directories;
        else { ++manifest.files; manifest.bytes += e.bytes; }
    }
}
