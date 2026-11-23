using System.Diagnostics;
using System.Text;

namespace CatRTools;

/// <summary>Finds catr.py and builds the same command line as catr.cmd:
/// "python -I catr.py ARGS", with python from the settings, CATR_PYTHON, or py -3.</summary>
internal static class CatrLauncher
{
    /// <summary>True when catr runs from the copy built into CatR-Tools.exe. A build without the bundle
    /// (dotnet build in the repo) finds the repo's catr folder instead.</summary>
    public static bool UsesBundle(AppSettings settings) =>
        Bundle.Catr is { } catr && File.Exists(Path.Combine(catr, "catr.py"));

    /// <summary>The workspace catr works in (backups, captures, crash logs): CATR_WORKSPACE, else
    /// "workspace" in catr's config.json, else (CatR-Tools.exe) the one it was built with if that folder
    /// exists, else Workspace next to the exe, else (a development copy) the workspace folder next to
    /// catr, as catr's config._workspace finds it.</summary>
    public static string? Workspace(AppSettings settings)
    {
        if (Environment.GetEnvironmentVariable("CATR_WORKSPACE") is { Length: > 0 } variable)
            return variable;
        if (SavedWorkspace() is { } saved)
            return saved;
        if (UsesBundle(settings))
            return Bundle.BuiltWorkspace is { } built && Directory.Exists(built) ? built
                : Path.Combine(Portable ? AppFolder : CatrHome, "Workspace");
        return FindCatrFolder(settings) is { } folder ? Path.GetFullPath(Path.Combine(folder, "..", "workspace")) : null;
    }

    static string? SavedWorkspace()
    {
        try
        {
            var path = ConfigFile;
            if (!File.Exists(path))
                return null;
            using var document = System.Text.Json.JsonDocument.Parse(File.ReadAllText(path));
            return document.RootElement.ValueKind == System.Text.Json.JsonValueKind.Object
                && document.RootElement.TryGetProperty("workspace", out var value)
                && value.ValueKind == System.Text.Json.JsonValueKind.String
                && value.GetString() is { Length: > 0 } saved ? saved : null;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Text.Json.JsonException)
        {
            return null;
        }
    }

    public static string? FindCatrFolder(AppSettings settings)
    {
        if (UsesBundle(settings))
            return Bundle.Catr;
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "catr.py")))
                return directory.FullName;
            var sibling = Path.Combine(directory.FullName, "catr");     // the repository's catr folder
            if (File.Exists(Path.Combine(sibling, "catr.py")))
                return sibling;
        }
        return null;
    }

    /// <summary>The PCFS folder next to the exe when there is one (FirstRun), else catr's
    /// pcfs.FALLBACK_ROOT: the PCFS folder in the workspace.</summary>
    public static string? DefaultPcfsRoot(AppSettings settings) =>
        Portable && Directory.Exists(FirstRun.PcfsFolder) ? FirstRun.PcfsFolder
        : Workspace(settings) is { } workspace
            ? Path.GetFullPath(Path.Combine(workspace, "PCFS"))
            : null;

    /// <summary>The one PCFS folder catr serves: pcfs_root in catr's config (set with catr pcfs root),
    /// else catr's built-in default. catr refuses a --root naming any other folder.</summary>
    public static string? PcfsRoot(AppSettings settings)
    {
        try
        {
            var path = ConfigFile;
            if (File.Exists(path))
            {
                using var document = System.Text.Json.JsonDocument.Parse(File.ReadAllText(path));
                if (document.RootElement.TryGetProperty("pcfs_root", out var value) && value.GetString() is { Length: > 0 } saved)
                    return saved;
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Text.Json.JsonException)
        {
        }
        return DefaultPcfsRoot(settings);
    }

    /// <summary>The folder CatR-Tools.exe runs from.</summary>
    public static string AppFolder { get; } = Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory);

    /// <summary>True when the settings live next to the exe: it is the published single-file
    /// CatR-Tools.exe (not a dotnet build in bin, which is thrown away with bin) and its folder can be
    /// written to (not, say, Program Files). CATR_CONFIG_FILE, when set, wins over both.</summary>
    public static bool Portable { get; } = Environment.GetEnvironmentVariable("CATR_CONFIG_FILE") is not { Length: > 0 }
        && SingleFile && CanWrite(AppFolder);

    /// <summary>A single-file app has no assembly file of its own, so its Location is empty.</summary>
#pragma warning disable IL3000
    static bool SingleFile => string.IsNullOrEmpty(typeof(CatrLauncher).Assembly.Location);
#pragma warning restore IL3000

    /// <summary>catr's settings file (IP, serial port, workspace, PCFS folder): CATR_CONFIG_FILE, else
    /// config.json next to the exe, else catr's own %APPDATA%\catr\config.json. Passed to catr as
    /// CATR_CONFIG_FILE, and catr's own config.json points at it ("config_file", PointCatrAtConfig), so
    /// catr.cmd uses the same settings. The services' state (serial.json, pcfs.json) stays in CatrHome.</summary>
    public static string ConfigFile { get; } = Environment.GetEnvironmentVariable("CATR_CONFIG_FILE") is { Length: > 0 } custom
        ? custom
        : Portable ? Path.Combine(AppFolder, "config.json") : Path.Combine(CatrHome, "config.json");

    static bool CanWrite(string folder)
    {
        try
        {
            var probe = Path.Combine(folder, $".write-test-{Environment.ProcessId}");
            File.WriteAllText(probe, "");
            File.Delete(probe);
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    /// <summary>Writes a JSON settings file the way catr's config.save does: a temporary file of this
    /// process's own (catr may be saving at the same moment), then replaced.</summary>
    public static void WriteJson(string path, System.Text.Json.Nodes.JsonNode values)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = $"{path}.{Environment.ProcessId}.tmp";
        File.WriteAllText(temporary, values.ToJsonString(new() { WriteIndented = true }) + "\n");
        File.Move(temporary, path, overwrite: true);
    }

    /// <summary>catr's config folder, as catr's config.HOME finds it.</summary>
    public static string CatrHome => Environment.GetEnvironmentVariable("CATR_CONFIG_DIR") is { Length: > 0 } custom
        ? custom
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "catr");

    public static string PythonDescription(AppSettings settings)
    {
        if (UsesBundle(settings))
            return $"built-in {Bundle.Version ?? "Python"}";
        var environment = Environment.GetEnvironmentVariable("CATR_PYTHON");
        return string.IsNullOrWhiteSpace(environment) ? "py -3" : $"{environment} (CATR_PYTHON)";
    }

    public static ProcessStartInfo StartInfo(AppSettings settings, IEnumerable<string> arguments)
    {
        var folder = FindCatrFolder(settings) ?? throw new InvalidOperationException(
            Bundle.Error ?? "catr.py not found: this build has no catr built in (build it with build.ps1).");
        var bundled = UsesBundle(settings);
        var python = bundled ? Bundle.Python : Environment.GetEnvironmentVariable("CATR_PYTHON");
        var info = new ProcessStartInfo(string.IsNullOrWhiteSpace(python) ? "py" : python)
        {
            UseShellExecute = false,
            WorkingDirectory = folder
        };
        info.Environment["CATR_CONFIG_FILE"] = ConfigFile;
        if (bundled)
        {
            // The unpacked copy lives under %LOCALAPPDATA% and is replaced by the next build: point
            // catr at the workspace, and keep its logs (audit, debug) in app data.
            var workspace = Workspace(settings);
            if (workspace is not null)
                info.Environment["CATR_WORKSPACE"] = workspace;
            info.Environment["CATR_LOGS"] = Path.Combine(CatrHome, "logs");
            if (Bundle.PcfsServer is { } server)
                info.Environment["CATR_PCFS_SERVER"] = server;
        }
        if (string.IsNullOrWhiteSpace(python))
            info.ArgumentList.Add("-3");
        info.ArgumentList.Add("-I");
        info.ArgumentList.Add(Path.Combine(folder, "catr.py"));
        foreach (var argument in arguments)
            info.ArgumentList.Add(argument);
        return info;
    }

    /// <summary>For display: catr ARGS with arguments quoted where needed.</summary>
    public static string Describe(IEnumerable<string> arguments) =>
        "catr " + string.Join(' ', arguments.Select(Quote));

    public static string Quote(string argument) =>
        argument.Length > 0 && !argument.Any(c => char.IsWhiteSpace(c) || c == '"')
            ? argument
            : "\"" + argument.Replace("\"", "\\\"") + "\"";

    /// <summary>Splits a typed command line: whitespace separates, double quotes group, \" is a quote.</summary>
    public static List<string> Split(string line)
    {
        var result = new List<string>();
        var current = new StringBuilder();
        bool quoted = false, any = false;
        for (var i = 0; i < line.Length; i++)
        {
            var c = line[i];
            if (c == '\\' && i + 1 < line.Length && line[i + 1] == '"')
            {
                current.Append('"');
                i++;
                any = true;
            }
            else if (c == '"')
            {
                quoted = !quoted;
                any = true;
            }
            else if (char.IsWhiteSpace(c) && !quoted)
            {
                if (any)
                    result.Add(current.ToString());
                current.Clear();
                any = false;
            }
            else
            {
                current.Append(c);
                any = true;
            }
        }
        if (any)
            result.Add(current.ToString());
        return result;
    }
}
