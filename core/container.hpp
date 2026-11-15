// BKNARC2 container and BNRLE1 block codec. All encoding/decoding is implemented here.
// Integers are unsigned little endian. CRC32 is corruption detection, not authentication.
static void putNumber(std::ostream& out, std::uint64_t value, unsigned width) {
    for (unsigned i = 0; i < width; ++i) { out.put(static_cast<char>(value & 255)); value >>= 8; }
    require(bool(out), "Container write failed.");
}
static std::uint64_t getNumber(std::istream& in, unsigned width) {
    std::uint64_t value = 0;
    for (unsigned i = 0; i < width; ++i) {
        const int c = in.get(); require(c != EOF, "Truncated container.");
        value |= static_cast<std::uint64_t>(c) << (i * 8);
    }
    return value;
}
static std::string readBytes(std::istream& in, std::size_t size) {
    std::string bytes(size, '\0');
    in.read(bytes.data(), static_cast<std::streamsize>(size));
    require(static_cast<std::size_t>(in.gcount()) == size, "Truncated container payload.");
    return bytes;
}
static std::string encodeRle(const std::string& bytes) {
    std::string out;
    for (std::size_t i = 0; i < bytes.size();) {
        std::size_t run = 1;
        while (run < 128 && i + run < bytes.size() && bytes[i + run] == bytes[i]) ++run;
        if (run >= 3) { out += static_cast<char>(128 + run - 1); out += bytes[i]; i += run; }
        else {
            const auto start = i;
            do {
                ++i;
                if (i + 2 < bytes.size() && bytes[i] == bytes[i + 1] && bytes[i] == bytes[i + 2]) break;
            } while (i < bytes.size() && i - start < 128);
            out += static_cast<char>(i - start - 1); out.append(bytes, start, i - start);
        }
    }
    return out;
}
static std::string decodeRle(const std::string& bytes, std::size_t expected) {
    std::string out;
    for (std::size_t i = 0; i < bytes.size();) {
        const unsigned control = static_cast<unsigned char>(bytes[i++]);
        const unsigned count = (control & 127) + 1;
        require(count <= expected - out.size(), "RLE output exceeds block bound.");
        if (control & 128) { require(i < bytes.size(), "Truncated RLE run."); out.append(count, bytes[i++]); }
        else { require(count <= bytes.size() - i, "Truncated RLE literal."); out.append(bytes, i, count); i += count; }
    }
    require(out.size() == expected, "RLE output length mismatch.");
    return out;
}
static void encodeFile(std::ostream& out, const fs::path& path, std::uint64_t size, bool compressed) {
    std::ifstream in(path, std::ios::binary); require(bool(in), "Cannot read file for encoding.");
    std::uint64_t done = 0;
    while (done < size) {
        cancelled();
        const auto raw = readBytes(in, static_cast<std::size_t>(std::min<std::uint64_t>(65536, size - done)));
        auto encoded = compressed ? encodeRle(raw) : raw;
        const bool rle = compressed && encoded.size() < raw.size();
        if (!rle) encoded = raw;
        putNumber(out, raw.size(), 4); putNumber(out, encoded.size(), 4); putNumber(out, rle, 1);
        putNumber(out, crcUpdate(0xffffffffU, raw.data(), raw.size()) ^ 0xffffffffU, 4);
        out.write(encoded.data(), static_cast<std::streamsize>(encoded.size()));
        require(bool(out), "Container write failed.");
        done += raw.size(); progress("encoding", done, size, 0, 1, text(path));
    }
    require(in.peek() == EOF, "Source grew during encoding.");
}
static void decodeFile(std::istream& in, const fs::path& path, std::uint64_t size) {
    std::ofstream out(path, std::ios::binary); require(bool(out), "Cannot create decoded file.");
    std::uint64_t done = 0;
    while (done < size) {
        cancelled();
        const auto rawSize = getNumber(in, 4), storedSize = getNumber(in, 4), mode = getNumber(in, 1), crc = getNumber(in, 4);
        require(rawSize > 0 && rawSize <= 65536 && rawSize <= size - done && storedSize > 0 && storedSize <= 65536 && mode <= 1,
                "Invalid block or resource limit exceeded.");
        auto bytes = readBytes(in, static_cast<std::size_t>(storedSize));
        if (mode) bytes = decodeRle(bytes, static_cast<std::size_t>(rawSize));
        require(bytes.size() == rawSize && (crcUpdate(0xffffffffU, bytes.data(), bytes.size()) ^ 0xffffffffU) == crc, "Block checksum mismatch.");
        out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        require(bool(out), "Decoded file write failed.");
        done += rawSize; progress("decoding", done, size, 0, 1, text(path));
    }
    out.close(); require(!out.fail(), "Decoded file close failed.");
}
static void unpackArchive(const fs::path& source, const fs::path& destination);
static void packArchive(const fs::path& source, const fs::path& destination) {
    const auto manifest = scan(source, false);
    require(manifest.bytes <= policy.maxOutput, "Archive exceeds configured output resource limit.");
    std::ofstream out(destination, std::ios::binary); require(bool(out), "Cannot create container.");
    out.write("BKNARC2\n", 8); putNumber(out, manifest.entries.size(), 4); putNumber(out, manifest.bytes, 8);
    for (const auto& entry : manifest.entries) {
        cancelled();
        const auto name = text(entry.relative);
        require(name.size() <= 4096, "Archive path too long.");
        putNumber(out, entry.directory ? 0 : 1, 1); putNumber(out, name.size(), 4);
        out.write(name.data(), static_cast<std::streamsize>(name.size())); putNumber(out, entry.bytes, 8);
        if (!entry.directory) encodeFile(out, source / entry.relative, entry.bytes, policy.compress);
    }
    out.close(); require(!out.fail(), "Container close failed.");
    const auto crc = checksum(destination).second;
    { std::ofstream footer(destination, std::ios::binary | std::ios::app); putNumber(footer, crc, 4); footer.close(); require(!footer.fail(), "Container footer write failed."); }
    const auto after = scan(source, false);
    require(after.entries.size() == manifest.entries.size(), "Source changed during packing.");
    for (std::size_t i = 0; i < after.entries.size(); ++i)
        require(after.entries[i].relative == manifest.entries[i].relative && after.entries[i].directory == manifest.entries[i].directory &&
                after.entries[i].bytes == manifest.entries[i].bytes && (manifest.entries[i].directory ||
                after.entries[i].modified == manifest.entries[i].modified), "Source changed during packing: " + text(manifest.entries[i].relative));
    const auto check = destination.parent_path() / (".backnest-check-" + uniqueId());
    try {
        unpackArchive(destination, check);
        for (const auto& e : manifest.entries) if (!e.directory)
            require(checksum(source / e.relative) == checksum(check / e.relative), "Encoded archive verification failed.");
        fs::remove_all(check);
    } catch (...) { std::error_code ec; fs::remove_all(check, ec); throw; }
}
static void unpackArchive(const fs::path& source, const fs::path& destination) {
    std::ifstream in(source, std::ios::binary); require(bool(in), "Cannot read container.");
    const auto fileSize = fs::file_size(source);
    require(fileSize >= 24, "Truncated container header/footer.");
    std::uint64_t remaining = fileSize - 4;
    std::uint32_t crc = 0xffffffffU;
    while (remaining) {
        cancelled();
        const auto bytes = readBytes(in, static_cast<std::size_t>(std::min<std::uint64_t>(remaining, 65536)));
        crc = crcUpdate(crc, bytes.data(), bytes.size()); remaining -= bytes.size();
    }
    require(getNumber(in, 4) == (crc ^ 0xffffffffU), "Container checksum mismatch.");
    in.seekg(0);
    require(readBytes(in, 8) == "BKNARC2\n", "Invalid container magic/version.");
    const auto count = getNumber(in, 4), total = getNumber(in, 8);
    require(count <= 200002 && total <= policy.maxOutput, "Archive resource limit exceeded.");
    require(fs::create_directory(destination), "Unpack staging directory must not exist.");
    std::uint64_t sum = 0;
    std::set<std::string> paths;
    for (std::uint64_t i = 0; i < count; ++i) {
        cancelled();
        const auto type = getNumber(in, 1), length = getNumber(in, 4);
        require(type <= 1 && length > 0 && length <= 4096, "Invalid container entry header.");
        const auto relative = safeRelative(readBytes(in, static_cast<std::size_t>(length)));
        const auto size = getNumber(in, 8);
        require(size <= total - sum && (type || size == 0), "Invalid container entry size.");
        sum += size;
        require(paths.insert(text(relative)).second, "Duplicate container path.");
        const auto target = destination / relative;
        require(fs::is_directory(target.parent_path()) && !fs::exists(target), "Missing parent or colliding container path.");
        if (!type) require(fs::create_directory(target), "Cannot create unpack directory.");
        else decodeFile(in, target, size);
    }
    getNumber(in, 4);
    require(sum == total && in.peek() == EOF, "Container totals or trailing bytes are invalid.");
}
static void entryJson(const Entry& entry) {
    std::cout << "{\"path\":" << json(text(entry.relative)) << ",\"kind\":" << json(entry.directory ? "directory" : "regular")
              << ",\"bytes\":" << entry.bytes << "}";
}
static bool extendedCommand(const std::vector<fs::path>& args) {
    if (args.size() == 3 && args[1] == "preview") {
        noLinks(args[2]); const auto source = resolvedAbsolute(args[2]);
        require(fs::is_directory(source), "Source directory does not exist.");
        const auto all = scan(source, false); auto included = all; filterManifest(source, included);
        std::set<std::string> names;
        std::cout << "{\"event\":\"result\",\"included\":[";
        bool first = true;
        for (const auto& e : included.entries) { if (!first) std::cout << ','; first = false; entryJson(e); names.insert(text(e.relative)); }
        std::cout << "],\"excluded\":["; first = true;
        for (const auto& e : all.entries) if (!names.count(text(e.relative))) { if (!first) std::cout << ','; first = false; entryJson(e); }
        std::cout << "],\"files\":" << included.files << ",\"bytes\":" << included.bytes << ",\"warnings\":[]}" << std::endl;
        return true;
    }
    if (args.size() != 4 || (args[1] != "pack" && args[1] != "unpack" && args[1] != "compress" && args[1] != "decompress")) return false;
    noLinks(args[2]); noLinks(args[3]);
    const auto source = resolvedAbsolute(args[2]), target = resolvedAbsolute(args[3]);
    disjoint(source, target); require(!fs::exists(target), "Codec destination must not exist.");
    require(fs::is_directory(target.parent_path()), "Destination parent must exist.");
    const auto stage = target.parent_path() / (".backnest-codec-" + uniqueId());
    Manifest resultManifest; resultManifest.source = text(source);
    try {
        if (args[1] == "pack") { require(fs::is_directory(source), "Expected source directory."); packArchive(source, stage); resultManifest = scan(source, false); }
        else if (args[1] == "unpack") { unpackArchive(source, stage); resultManifest = scan(stage, false); }
        else {
            require(fs::is_regular_file(source), "Expected regular codec source file.");
            if (args[1] == "compress") {
                const auto size = fs::file_size(source);
                const auto stamp = fs::last_write_time(source);
                require(size <= policy.maxOutput, "Codec resource limit exceeded.");
                std::ofstream out(stage, std::ios::binary); out.write("BNRLE1\n", 7); putNumber(out, size, 8);
                encodeFile(out, source, size, true); out.close(); require(!out.fail(), "Codec close failed.");
                require(fs::last_write_time(source) == stamp && fs::file_size(source) == size, "Source changed during compression.");
                const auto check = target.parent_path() / (".backnest-check-" + uniqueId());
                try {
                    std::ifstream encoded(stage, std::ios::binary); encoded.seekg(15); decodeFile(encoded, check, size);
                    require(checksum(source) == checksum(check), "Compressed file verification failed."); fs::remove(check);
                } catch (...) { std::error_code ec; fs::remove(check, ec); throw; }
                resultManifest.bytes = size;
            } else {
                std::ifstream in(source, std::ios::binary); require(readBytes(in, 7) == "BNRLE1\n", "Invalid RLE magic/version.");
                const auto size = getNumber(in, 8); require(size <= policy.maxOutput, "Codec resource limit exceeded.");
                decodeFile(in, stage, size); require(in.peek() == EOF, "Trailing RLE data."); resultManifest.bytes = size;
            }
            resultManifest.files = 1;
        }
        commit(); fs::rename(stage, target); result(target, resultManifest, text(args[1]));
    } catch (...) { std::error_code ec; fs::remove_all(stage, ec); throw; }
    return true;
}
