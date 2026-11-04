// C++17 backup engine: ordinary files and directory trees, no third-party dependencies.
#include "backend_interfaces.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

static void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}
static std::string text(const fs::path& path) { return path.generic_u8string(); }
static std::string json(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
            else out << char(c);
        }
    }
    return out.str() + '"';
}
static bool inside(const fs::path& path, const fs::path& root) {
    auto p = path.begin();
    for (auto r = root.begin(); r != root.end(); ++r, ++p) {
        if (p == path.end() || *p != *r) return false;
    }
    return true;
}
static void disjoint(const fs::path& a, const fs::path& b) {
    require(!inside(a, b) && !inside(b, a), "Source and destination must not contain each other.");
}
static fs::path resolvedAbsolute(const fs::path& path) {
    require(!path.empty() && path.is_absolute(), "Use an absolute directory path.");
    return fs::weakly_canonical(path);
}
static void noLinks(const fs::path& path) {
    fs::path current;
    for (const auto& component : path) {
        current /= component;
        require(!fs::is_symlink(fs::symlink_status(current)), "Symbolic links are not supported: " + text(current));
    }
}
static std::string uniqueId() {
    std::random_device random;
    std::ostringstream out;
    out << std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::system_clock::now().time_since_epoch()).count()
        << '-' << std::hex << random();
    return out.str();
}

struct Entry {
    bool directory = false;
    fs::path relative;
    std::uintmax_t bytes = 0;
    std::uint32_t crc = 0;
    fs::file_time_type modified{};
};
struct Manifest {
    std::string source;
    std::vector<Entry> entries;
    std::uintmax_t bytes = 0;
    std::size_t files = 0;
    std::size_t directories = 0;
};
static fs::path safeRelative(const std::string& name) {
    require(!name.empty() && name.find('\0') == std::string::npos && name.find('\\') == std::string::npos,
            "Unsupported or unsafe relative path in backup.");
    fs::path path = fs::u8path(name);
    require(!path.is_absolute() && !path.has_root_name() && !path.has_root_directory(), "Absolute paths are not allowed in a backup manifest.");
    for (const auto& part : path) require(part != ".." && part != "." && !part.empty(), "Unsafe relative path in backup.");
    return path;
}
static void progress(const std::string& phase, std::uintmax_t bytes, std::uintmax_t totalBytes,
                     std::size_t files, std::size_t totalFiles, const std::string& path = "") {
    std::cout << "{\"event\":\"progress\",\"phase\":" << json(phase)
              << ",\"bytes\":" << bytes << ",\"totalBytes\":" << totalBytes
              << ",\"files\":" << files << ",\"totalFiles\":" << totalFiles
              << ",\"path\":" << json(path) << "}" << std::endl;
}
static Manifest scan(const fs::path& source) {
    Manifest manifest;
    manifest.source = text(source);
    progress("scanning", 0, 0, 0, 0);
    for (const auto& item : fs::recursive_directory_iterator(source)) {
        Entry entry;
        entry.relative = safeRelative(text(item.path().lexically_relative(source)));
        const auto status = item.symlink_status();
        require(!fs::is_symlink(status), "Symbolic links are not supported in the basic demo: " + text(item.path()));
        entry.directory = fs::is_directory(status);
        require(entry.directory || fs::is_regular_file(status), "Unsupported file type: " + text(item.path()));
        if (entry.directory) ++manifest.directories;
        else {
            entry.bytes = item.file_size();
            entry.modified = item.last_write_time();
            require(manifest.bytes <= std::numeric_limits<std::uintmax_t>::max() - entry.bytes, "Total file size overflow.");
            manifest.bytes += entry.bytes;
            ++manifest.files;
        }
        manifest.entries.push_back(entry);
        require(manifest.entries.size() <= 200000, "Basic demo supports at most 200,000 entries per backup.");
    }
    std::sort(manifest.entries.begin(), manifest.entries.end(), [](const Entry& a, const Entry& b) {
        if (a.directory != b.directory) return a.directory > b.directory;
        return text(a.relative) < text(b.relative);
    });
    return manifest;
}

// CRC32 detects accidental corruption; this is not encryption or an authenticity check.
static std::uint32_t crcUpdate(std::uint32_t crc, const char* data, std::size_t size) {
    static const auto table = [] {
        std::array<std::uint32_t, 256> values{};
        for (std::uint32_t i = 0; i < 256; ++i) {
            std::uint32_t v = i;
            for (int bit = 0; bit < 8; ++bit) v = (v >> 1) ^ ((v & 1) ? 0xedb88320U : 0);
            values[i] = v;
        }
        return values;
    }();
    for (std::size_t i = 0; i < size; ++i) crc = table[(crc ^ static_cast<unsigned char>(data[i])) & 255] ^ (crc >> 8);
    return crc;
}
static std::pair<std::uintmax_t, std::uint32_t> checksum(const fs::path& path) {
    require(fs::is_regular_file(fs::symlink_status(path)), "Expected a regular file: " + text(path));
    std::ifstream in(path, std::ios::binary);
    require(bool(in), "Cannot read file: " + text(path));
    std::array<char, 64 * 1024> buffer{};
    std::uintmax_t bytes = 0;
    std::uint32_t crc = 0xffffffffU;
    while (in) {
        in.read(buffer.data(), buffer.size());
        const auto count = in.gcount();
        crc = crcUpdate(crc, buffer.data(), static_cast<std::size_t>(count));
        bytes += static_cast<std::uintmax_t>(count);
    }
    require(in.eof(), "Read failed: " + text(path));
    return {bytes, crc ^ 0xffffffffU};
}
static std::uint32_t copyChecked(const fs::path& source, const fs::path& destination, const Entry& entry,
                                 std::uintmax_t& processed, std::size_t done, const Manifest& manifest) {
    require(fs::is_regular_file(fs::symlink_status(source)), "Source is no longer a regular file: " + text(source));
    require(!fs::exists(destination), "Destination name collision: " + text(destination));
    std::ifstream in(source, std::ios::binary);
    require(bool(in), "Cannot read file: " + text(source));
    std::ofstream out(destination, std::ios::binary);
    require(bool(out), "Cannot write file: " + text(destination));
    std::array<char, 64 * 1024> buffer{};
    std::uint32_t crc = 0xffffffffU;
    std::uintmax_t bytes = 0;
    auto last = Clock::now();
    while (in) {
        in.read(buffer.data(), buffer.size());
        const auto count = in.gcount();
        out.write(buffer.data(), count);
        require(bool(out), "Write failed (check available disk space): " + text(destination));
        crc = crcUpdate(crc, buffer.data(), static_cast<std::size_t>(count));
        bytes += static_cast<std::uintmax_t>(count);
        processed += static_cast<std::uintmax_t>(count);
        if (Clock::now() - last > std::chrono::milliseconds(150)) {
            progress("copying", processed, manifest.bytes, done, manifest.files, text(entry.relative));
            last = Clock::now();
        }
    }
    require(in.eof(), "Read failed: " + text(source));
    out.close();
    require(!out.fail(), "Failed to finish writing: " + text(destination));
    require(bytes == entry.bytes, "Source file changed during copying: " + text(source));
    const auto result = checksum(destination);
    crc ^= 0xffffffffU;
    require(result.first == bytes && result.second == crc, "Copied file verification failed: " + text(destination));
    return crc;
}
static void writeManifest(const fs::path& path, const Manifest& manifest) {
    std::ofstream out(path, std::ios::binary);
    require(bool(out), "Cannot create backup manifest.");
    out << "BACKNEST 1\n" << manifest.files << ' ' << manifest.directories << ' ' << manifest.bytes << '\n'
        << std::quoted(manifest.source) << '\n';
    for (const auto& entry : manifest.entries) {
        if (entry.directory) out << "D " << std::quoted(text(entry.relative)) << '\n';
        else out << "F " << entry.bytes << ' ' << entry.crc << ' ' << std::quoted(text(entry.relative)) << '\n';
    }
    out.close();
    require(!out.fail(), "Failed to finish backup manifest.");
}
static Manifest readManifest(const fs::path& snapshot) {
    noLinks(snapshot);
    require(fs::is_directory(snapshot), "Backup version directory does not exist.");
    const auto manifestPath = snapshot / "manifest.bkn";
    require(fs::is_regular_file(fs::symlink_status(manifestPath)), "Backup manifest is missing or is a symbolic link.");
    require(fs::file_size(manifestPath) <= 64 * 1024 * 1024, "Backup manifest is too large.");
    std::ifstream in(manifestPath, std::ios::binary);
    std::string magic;
    int version = 0;
    Manifest manifest;
    in >> magic >> version >> manifest.files >> manifest.directories >> manifest.bytes >> std::quoted(manifest.source);
    require(bool(in) && magic == "BACKNEST" && version == 1, "Invalid or unsupported backup manifest.");
    require(manifest.files <= 200000 && manifest.directories <= 200000 && manifest.files + manifest.directories <= 200000,
            "Backup contains too many entries.");
    std::map<std::string, bool> paths;
    std::size_t files = 0, directories = 0;
    std::uintmax_t bytes = 0;
    for (std::size_t i = 0; i < manifest.files + manifest.directories; ++i) {
        char type = 0;
        std::string name;
        Entry entry;
        in >> type;
        require(type == 'D' || type == 'F', "Invalid entry type in backup manifest.");
        entry.directory = type == 'D';
        if (!entry.directory) {
            std::uint64_t crc = 0;
            in >> entry.bytes >> crc;
            require(bool(in) && crc <= 0xffffffffULL && bytes <= std::numeric_limits<std::uintmax_t>::max() - entry.bytes,
                    "Invalid size or checksum in manifest.");
            entry.crc = static_cast<std::uint32_t>(crc);
            bytes += entry.bytes;
            ++files;
        } else ++directories;
        in >> std::quoted(name);
        require(bool(in), "Truncated backup manifest.");
        entry.relative = safeRelative(name);
        require(paths.emplace(text(entry.relative), entry.directory).second, "Duplicate entry in backup manifest.");
        manifest.entries.push_back(entry);
    }
    in >> std::ws;
    require(in.eof() && files == manifest.files && directories == manifest.directories && bytes == manifest.bytes,
            "Backup manifest totals do not match its contents.");
    for (const auto& entry : manifest.entries) {
        const auto parent = entry.relative.parent_path();
        if (!parent.empty()) require(paths.count(text(parent)) && paths.at(text(parent)), "Missing parent directory in manifest.");
    }
    return manifest;
}
static Manifest verify(const fs::path& snapshot) {
    auto manifest = readManifest(snapshot);
    const auto data = snapshot / "data";
    require(fs::is_directory(fs::symlink_status(data)), "Backup data directory is missing or is a symbolic link.");
    std::map<std::string, const Entry*> expected;
    for (const auto& entry : manifest.entries) expected[text(entry.relative)] = &entry;
    std::size_t entries = 0, done = 0;
    std::uintmax_t bytes = 0;
    for (const auto& item : fs::recursive_directory_iterator(data)) {
        const auto relative = text(item.path().lexically_relative(data));
        const auto found = expected.find(relative);
        require(found != expected.end(), "Unexpected file in backup: " + relative);
        const auto& entry = *found->second;
        const auto status = item.symlink_status();
        require(!fs::is_symlink(status), "Symbolic links are not allowed in backup data.");
        if (entry.directory) require(fs::is_directory(status), "Expected a directory: " + relative);
        else {
            require(fs::is_regular_file(status), "Expected a regular file: " + relative);
            const auto actual = checksum(item.path());
            require(actual.first == entry.bytes && actual.second == entry.crc, "Backup checksum mismatch: " + relative);
            bytes += actual.first;
            progress("verifying", bytes, manifest.bytes, ++done, manifest.files, relative);
        }
        ++entries;
    }
    require(entries == expected.size(), "Files or directories are missing from backup.");
    return manifest;
}
static void result(const fs::path& path, const Manifest& manifest, const std::string& operation) {
    std::cout << "{\"event\":\"result\",\"operation\":" << json(operation)
              << ",\"path\":" << json(text(path)) << ",\"source\":" << json(manifest.source)
              << ",\"files\":" << manifest.files << ",\"directories\":" << manifest.directories
              << ",\"bytes\":" << manifest.bytes << ",\"verified\":true}" << std::endl;
}
static void backup(const fs::path& sourceArg, const fs::path& rootArg) {
    noLinks(sourceArg);
    noLinks(rootArg);
    const auto source = resolvedAbsolute(sourceArg), root = resolvedAbsolute(rootArg);
    require(fs::is_directory(source), "Source directory does not exist.");
    disjoint(source, root);
    auto manifest = scan(source);
    fs::create_directories(root);
    const auto id = uniqueId();
    const auto stage = root / (".partial-" + id), final = root / ("snapshot-" + id);
    require(fs::create_directory(stage), "Cannot create temporary backup directory.");
    try {
        const auto data = stage / "data";
        fs::create_directory(data);
        std::uintmax_t processed = 0;
        std::size_t done = 0;
        for (auto& entry : manifest.entries) {
            const auto destination = data / entry.relative;
            if (entry.directory) require(fs::create_directory(destination), "Directory name collision: " + text(entry.relative));
            else {
                const auto file = source / entry.relative;
                entry.crc = copyChecked(file, destination, entry, processed, done, manifest);
                require(fs::file_size(file) == entry.bytes && fs::last_write_time(file) == entry.modified,
                        "Source file changed during backup: " + text(file));
                progress("copying", processed, manifest.bytes, ++done, manifest.files, text(entry.relative));
            }
        }
        // Check that entries were not added, removed or changed during the operation.
        const auto after = scan(source);
        require(after.entries.size() == manifest.entries.size(), "Source directory changed during backup.");
        for (std::size_t i = 0; i < after.entries.size(); ++i) {
            const auto& beforeEntry = manifest.entries[i];
            const auto& afterEntry = after.entries[i];
            require(beforeEntry.relative == afterEntry.relative && beforeEntry.directory == afterEntry.directory &&
                    (beforeEntry.directory || (beforeEntry.bytes == afterEntry.bytes && beforeEntry.modified == afterEntry.modified)),
                    "Source directory changed during backup.");
        }
        writeManifest(stage / "manifest.bkn", manifest);
        verify(stage);
        fs::rename(stage, final);
        result(final, manifest, "backup");
    } catch (...) {
        std::error_code ignored;
        fs::remove_all(stage, ignored);
        throw;
    }
}
static void restore(const fs::path& snapshotArg, const fs::path& targetArg) {
    noLinks(snapshotArg);
    noLinks(targetArg);
    const auto snapshot = resolvedAbsolute(snapshotArg), target = resolvedAbsolute(targetArg);
    disjoint(snapshot, target);
    require(!fs::exists(target) || (fs::is_directory(target) && fs::is_empty(target)),
            "Restore destination must be a new or empty directory. Existing files will not be overwritten.");
    auto manifest = verify(snapshot); // Validate all contents before creating output.
    fs::create_directories(target.parent_path());
    const auto stage = target.parent_path() / (".backnest-restore-" + uniqueId());
    require(fs::create_directory(stage), "Cannot create temporary restore directory.");
    bool removedEmptyTarget = false;
    try {
        auto entries = manifest.entries;
        std::sort(entries.begin(), entries.end(), [](const Entry& a, const Entry& b) {
            if (a.directory != b.directory) return a.directory > b.directory;
            return text(a.relative) < text(b.relative);
        });
        std::uintmax_t processed = 0;
        std::size_t done = 0;
        for (const auto& entry : entries) {
            const auto destination = stage / entry.relative;
            if (entry.directory) require(fs::create_directory(destination), "Directory name collision during restore.");
            else {
                const auto actual = copyChecked(snapshot / "data" / entry.relative, destination, entry, processed, done, manifest);
                require(actual == entry.crc, "Backup changed during restore: " + text(entry.relative));
                progress("copying", processed, manifest.bytes, ++done, manifest.files, text(entry.relative));
            }
        }
        noLinks(targetArg);
        if (fs::exists(target)) {
            require(fs::is_directory(target) && fs::is_empty(target), "Restore destination changed; no existing files were overwritten.");
            removedEmptyTarget = fs::remove(target); // Never recursively delete the destination.
            require(removedEmptyTarget, "Cannot replace empty destination directory.");
        }
        fs::rename(stage, target);
        result(target, manifest, "restore");
    } catch (...) {
        std::error_code ignored;
        fs::remove_all(stage, ignored);
        if (removedEmptyTarget && !fs::exists(target)) fs::create_directory(target, ignored);
        throw;
    }
}
static int run(const std::vector<fs::path>& args) {
    try {
        if (args.size() == 4 && args[1] == "backup") backup(args[2], args[3]);
        else if (args.size() == 4 && args[1] == "restore") restore(args[2], args[3]);
        else if (args.size() == 3 && args[1] == "verify") {
            const auto snapshot = resolvedAbsolute(args[2]);
            result(snapshot, verify(snapshot), "verify");
        } else {
            std::cerr << "Usage:\n  backnest backup <absolute-source-directory> <absolute-backup-root>\n"
                         "  backnest restore <absolute-snapshot-directory> <absolute-new-or-empty-directory>\n"
                         "  backnest verify <absolute-snapshot-directory>\n";
            return 2;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cout << "{\"event\":\"error\",\"message\":" << json(error.what()) << "}" << std::endl;
        return 1;
    }
}
#ifdef _WIN32
int wmain(int argc, wchar_t* argv[]) {
    std::vector<fs::path> args;
    for (int i = 0; i < argc; ++i) args.emplace_back(argv[i]);
    return run(args);
}
#else
int main(int argc, char* argv[]) {
    std::vector<fs::path> args;
    for (int i = 0; i < argc; ++i) args.emplace_back(argv[i]);
    return run(args);
}
#endif
