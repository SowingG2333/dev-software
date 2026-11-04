#pragma once
// Backend extension ports. Only the CLI backup/restore implementation exists today.
// Archive/compression/core algorithms MUST be implemented by this project.
// Cryptography MAY use a reviewed crypto library; filesystem and serialization APIs are allowed.
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <istream>
#include <ostream>
#include <string>
#include <vector>

namespace backnest::ports {
namespace fs = std::filesystem;
struct Progress { std::string phase, relativePath; std::uint64_t files=0, totalFiles=0, bytes=0, totalBytes=0; };
using ProgressSink = std::function<void(const Progress&)>;
using Cancellation = std::atomic_bool;
struct Owner { std::string platform, id, displayName; }; // POSIX UID/GID or Windows SID, not a login name assumption.
struct Filters {
    std::vector<fs::path> includePaths, excludePaths;
    std::vector<std::string> types, names;
    std::string timeField, timeFrom, timeTo; // RFC3339 with explicit timezone.
    std::uint64_t minBytes=0, maxBytes=0; // maxBytes=0 means no upper bound.
    std::vector<Owner> users;
};
struct FileTypePolicy { std::string symlinks="reject", hardlinks="copy_contents", specialFiles="reject"; };
struct MetadataPolicy { std::vector<std::string> preserve; std::string unsupported="fail"; };
struct CodecOptions { std::string algorithm; std::uint64_t blockBytes=65536, maxOutputBytes=0; };
struct EntryMetadata {
    fs::path relativePath; std::string kind, modifiedAt, createdAt, accessedAt, permissions, acl, xattrs;
    Owner owner; std::uint64_t bytes=0;
};
struct Result { fs::path path; std::uint64_t files=0, directories=0, bytes=0; bool verified=false; std::vector<std::string> warnings; };
struct BackupRequest {
    fs::path source, destination; std::string mode="full";
    Filters filters; FileTypePolicy fileTypes; MetadataPolicy metadata;
    CodecOptions archive, compression, encryption;
    std::string secretRef; // Secret-service reference only: no plaintext passwords in tasks/manifests/logs.
};
struct RestoreRequest { fs::path snapshot, destination; std::string conflictPolicy="require_empty", secretRef; MetadataPolicy metadata; };
struct FilterPreview { std::vector<EntryMetadata> included, excluded; std::uint64_t files=0, bytes=0; std::vector<std::string> warnings; };
struct AlgorithmInfo { std::string id, version; bool selfImplemented=false; std::vector<std::string> supportedPlatforms; };

struct IBackupEngine {
    virtual ~IBackupEngine()=default;
    virtual Result backup(const BackupRequest&, ProgressSink, const Cancellation&)=0;
    virtual Result restore(const RestoreRequest&, ProgressSink, const Cancellation&)=0;
    virtual Result verify(const fs::path&, ProgressSink, const Cancellation&)=0;
};
struct IFileTypeHandler {
    virtual ~IFileTypeHandler()=default;
    virtual std::vector<EntryMetadata> inspect(const fs::path&, const FileTypePolicy&)=0;
    virtual void backupEntry(const fs::path& source, const fs::path& destination, const FileTypePolicy&)=0;
    virtual void restoreEntry(const fs::path& source, const fs::path& destination, const FileTypePolicy&)=0;
};
struct IMetadataService {
    virtual ~IMetadataService()=default;
    virtual EntryMetadata read(const fs::path&)=0;
    virtual std::vector<std::string> apply(const fs::path&, const EntryMetadata&, const MetadataPolicy&)=0;
};
struct IFilterEngine {
    virtual ~IFilterEngine()=default;
    virtual std::vector<std::string> validate(const Filters&)=0;
    virtual FilterPreview preview(const fs::path&, const Filters&, const FileTypePolicy&)=0;
};
struct IArchiveCodec {
    virtual ~IArchiveCodec()=default;
    virtual std::vector<AlgorithmInfo> algorithms() const=0;
    virtual Result pack(const fs::path&, const fs::path&, const CodecOptions&, ProgressSink, const Cancellation&)=0;
    virtual Result unpack(const fs::path&, const fs::path&, const CodecOptions&, ProgressSink, const Cancellation&)=0;
    virtual std::vector<EntryMetadata> list(const fs::path&)=0;
};
struct ICompressionCodec {
    virtual ~ICompressionCodec()=default;
    virtual std::vector<AlgorithmInfo> algorithms() const=0;
    virtual void compress(std::istream&, std::ostream&, const CodecOptions&, ProgressSink, const Cancellation&)=0;
    virtual void decompress(std::istream&, std::ostream&, const CodecOptions&, ProgressSink, const Cancellation&)=0;
};
struct ICryptoProvider { // A vetted cryptography library is explicitly allowed.
    virtual ~ICryptoProvider()=default;
    virtual std::vector<AlgorithmInfo> algorithms() const=0;
    virtual void encrypt(std::istream&, std::ostream&, const CodecOptions&, const std::string& secretRef, ProgressSink, const Cancellation&)=0;
    virtual void decrypt(std::istream&, std::ostream&, const CodecOptions&, const std::string& secretRef, ProgressSink, const Cancellation&)=0;
};
struct Schedule { std::string id, taskId, timezone, expression, missedRunPolicy="skip"; bool enabled=false; };
struct IScheduler {
    virtual ~IScheduler()=default;
    virtual std::vector<Schedule> list()=0;
    virtual Schedule save(const Schedule&)=0;
    virtual void remove(const std::string& id)=0;
    virtual std::vector<std::string> nextRuns(const std::string& id)=0;
};
struct WatchRequest { std::string taskId; fs::path source; std::uint64_t debounceMs=500; std::vector<fs::path> excludedPaths; };
struct IFileWatcher {
    virtual ~IFileWatcher()=default;
    virtual std::string start(const WatchRequest&)=0;
    virtual Progress status(const std::string& watchId)=0;
    virtual void stop(const std::string& watchId)=0;
};
struct RemoteConfig { std::string id, name, endpoint, credentialRef; };
struct SnapshotRecord { std::string id, taskId, remoteId, createdAt; fs::path manifestPath; std::uint64_t files=0, bytes=0; bool verified=false; };
struct IRemoteStorage {
    virtual ~IRemoteStorage()=default;
    virtual RemoteConfig configure(const RemoteConfig&)=0;
    virtual void test(const std::string& remoteId)=0;
    virtual Result upload(const std::string& remoteId, const fs::path&, const std::string& resumeToken, ProgressSink, const Cancellation&)=0;
    virtual Result download(const std::string& remoteId, const std::string& snapshotId, const fs::path&, ProgressSink, const Cancellation&)=0;
};
struct ISnapshotCatalog { // Same port can back the local index or remote metadata management.
    virtual ~ISnapshotCatalog()=default;
    virtual std::vector<SnapshotRecord> list(const std::string& scopeId)=0;
    virtual SnapshotRecord registerVerified(const SnapshotRecord&)=0;
    virtual SnapshotRecord get(const std::string& snapshotId)=0;
    virtual void removeIndexOnly(const std::string& snapshotId)=0;
};
struct UserRecord { std::string id, username; std::vector<std::string> roles; };
struct IUserService {
    virtual ~IUserService()=default;
    virtual std::vector<UserRecord> list()=0;
    virtual UserRecord save(const UserRecord&)=0;
    virtual void remove(const std::string& id)=0;
    virtual std::string login(const std::string& username, const std::string& secretRef)=0;
    virtual void revokeSession(const std::string& sessionId)=0;
};
struct IWorkspaceRepository { // Serialize only non-secret configuration DTOs across this boundary.
    virtual ~IWorkspaceRepository()=default;
    virtual std::string listTasksJson()=0;
    virtual std::string saveTaskJson(const std::string& validatedTaskJson)=0;
    virtual void deleteTaskConfig(const std::string& id)=0;
    virtual std::string readSettingsJson()=0;
    virtual void saveSettingsJson(const std::string& validatedSettingsJson)=0;
    virtual std::string readActivityJson()=0;
    virtual std::string overviewJson()=0;
};
} // namespace backnest::ports
