using System.Text.Json;
using System.Text.Json.Serialization;

namespace CatRTools;

/// <summary>CatR-Tools' own window settings (window, history, Titles options), in %APPDATA%\catr\app\settings.json
/// next to catr's caches ("app data"). The console IP, serial port and PCFS folder are in catr's config.json.</summary>
internal sealed class AppSettings
{
    public string? LastInstallSource { get; set; }
    public bool InstallKeep { get; set; }
    public bool InstallSizeOnly { get; set; }
    public string? LastRunTarget { get; set; }
    public bool OpenLogAfterRun { get; set; } = true;

    /// <summary>Titles page: show system apps, system data and applets (menus are always shown).</summary>
    public bool ShowSystemTitles { get; set; }

    /// <summary>Titles page: leave out titles whose meta.xml gave no name.</summary>
    public bool HideUnnamedTitles { get; set; }

    /// <summary>Removing a PCFS title's folder: "Trash" (Recycle Bin), "Delete" (permanently) or "Ask".</summary>
    public string PcfsRemoval { get; set; } = "Trash";

    /// <summary>Closing the window with catr's background services running: "Ask", "Stop" or "Leave".</summary>
    public string OnClose { get; set; } = "Ask";

    /// <summary>"System", "Light" or "Dark". Applied at start.</summary>
    public string Theme { get; set; } = "System";
    public int LogFontSize { get; set; } = 10;
    public bool LogWrap { get; set; } = true;
    public int[]? LogBounds { get; set; }
    public List<string> CommandHistory { get; set; } = [];
    public List<string> CosHistory { get; set; } = [];

    public static string Folder { get; } = Path.Combine(CatrLauncher.CatrHome, "app");

    public static string FilePath => Path.Combine(Folder, "settings.json");

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    public static AppSettings Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(FilePath), Options) ?? new();
        }
        catch (Exception exception) when (exception is IOException or JsonException or UnauthorizedAccessException)
        {
            // A damaged settings file falls back to defaults; it is rewritten on the next save.
        }
        return new();
    }

    public void Save()
    {
        Directory.CreateDirectory(Folder);
        var temporary = FilePath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(this, Options));
        File.Move(temporary, FilePath, overwrite: true);
    }

    public static void Remember(List<string> history, string entry, int limit = 50)
    {
        history.Remove(entry);
        history.Add(entry);
        if (history.Count > limit)
            history.RemoveRange(0, history.Count - limit);
    }
}
