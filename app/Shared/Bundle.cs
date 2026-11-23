using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;

namespace CatRTools;

/// <summary>catr, a private Python runtime and pcfsserver.exe, embedded in CatR-Tools.exe as
/// catr-bundle.zip (built by Bundle\make_bundle.py). Unpacked once per bundle version to
/// %LOCALAPPDATA%\CatR-Tools\bundle-HASH; older folders are left alone, since a PCFS keeper or
/// serial service started by an older version may still run from one.</summary>
internal static class Bundle
{
    private const string ResourceName = "catr-bundle.zip";
    private static readonly Lazy<string?> folder = new(Unpack);

    public static string Root => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CatR-Tools");

    /// <summary>The unpacked bundle, or null when this build has none (or it couldn't be unpacked).</summary>
    public static string? Folder => folder.Value;
    public static string? Error { get; private set; }

    public static string? Python => Folder is { } f ? Path.Combine(f, "python", "python.exe") : null;
    public static string? Catr => Folder is { } f ? Path.Combine(f, "catr") : null;
    public static string? PcfsServer => Folder is { } f ? Path.Combine(f, "pcfs", "pcfsserver.exe") : null;

    /// <summary>The workspace the bundle was built with (bundle.json), or null in a release build.</summary>
    public static string? BuiltWorkspace
    {
        get
        {
            if (Folder is not { } f) return null;
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(f, "bundle.json")));
                return document.RootElement.TryGetProperty("workspace", out var value) ? value.GetString() : null;
            }
            catch (Exception exception) when (exception is IOException or JsonException)
            {
                return null;
            }
        }
    }

    public static string? Version
    {
        get
        {
            if (Folder is not { } f) return null;
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(f, "bundle.json")));
                var root = document.RootElement;
                return $"Python {root.GetProperty("python").GetString()}, built {root.GetProperty("built").GetString()}";
            }
            catch (Exception exception) when (exception is IOException or JsonException or KeyNotFoundException)
            {
                return null;
            }
        }
    }

    private static string? Unpack()
    {
        using var stream = Assembly.GetEntryAssembly()?.GetManifestResourceStream(ResourceName);
        if (stream is null)
            return null;
        try
        {
            var bytes = new byte[stream.Length];
            stream.ReadExactly(bytes);
            var hash = Convert.ToHexString(SHA256.HashData(bytes))[..12].ToLowerInvariant();
            var target = Path.Combine(Root, "bundle-" + hash);
            if (File.Exists(Path.Combine(target, ".complete")))
                return target;
            var staging = target + ".unpacking-" + Environment.ProcessId;
            if (Directory.Exists(staging))
                Directory.Delete(staging, recursive: true);
            using (var archive = new ZipArchive(new MemoryStream(bytes)))
                archive.ExtractToDirectory(staging);
            File.WriteAllText(Path.Combine(staging, ".complete"), hash);
            try
            {
                if (Directory.Exists(target))
                    Directory.Delete(target, recursive: true);   // an earlier unpack that didn't finish
                Directory.Move(staging, target);
            }
            catch (IOException) when (File.Exists(Path.Combine(target, ".complete")))
            {
                Directory.Delete(staging, recursive: true);      // another window unpacked it meanwhile
            }
            return target;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or InvalidDataException)
        {
            Error = $"Couldn't unpack the built-in catr to {Root}: {exception.Message}";
            return null;
        }
    }
}
