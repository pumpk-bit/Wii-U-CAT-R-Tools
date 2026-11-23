using System.Text.Json;

namespace CatRTools;

internal sealed record TitleInfo(string Id, string? Name, int? Version, string Location, string? Path)
{
    /// <summary>For a title staged in the PCFS folder: its folder on this PC (Location "pcfs").</summary>
    public string? HostFolder { get; init; }

    public bool IsPcfs => HostFolder is not null;

    public bool IsSystem => Kind is "System app" or "System data" or "Applet" or "Other";

    /// <summary>app.xml app_type as 8 hex digits (from MCP's title list or the staged app.xml).</summary>
    public string? AppType { get; init; }

    /// <summary>What System Config Tool lists as type MENU: app_type 0x90000001 (Wii U Menu, System Config
    /// Tool, CAT Recovery Menu, Kiosk Menu, SugarBootTool). Shown even when system titles are hidden.
    /// A title list cached before app_type was kept falls back to the name.</summary>
    public bool IsMenu => AppType is { } type
        ? type.Equals("90000001", StringComparison.OrdinalIgnoreCase)
        : Kind == "System app" && Name?.Contains("Menu", StringComparison.OrdinalIgnoreCase) == true;

    /// <summary>The Titles page leaves out SLC, DRH (GamePad) and BT titles, and MCP's entries for
    /// PCFS (hfio) copies, which are listed from the PCFS folder instead.</summary>
    public bool HiddenDevice => Location is "slc" or "drh" or "bt"
                                || Path?.Contains("hfio", StringComparison.OrdinalIgnoreCase) == true;

    public string High => Id.Length >= 8 ? Id[..8] : Id;

    public string Kind => High switch
    {
        "00050000" => "Game",
        "0005000e" => "Update",
        "0005000c" => "DLC",
        "00050002" => "Demo",
        "00050010" => "System app",
        "0005001b" => "System data",
        "00050030" => "Applet",
        _ => "Other"
    };

    /// <summary>catr uninstall refuses system titles, SCT and the Recovery Menu anyway; only offer it here.</summary>
    public bool Removable => Kind is "Game" or "Update" or "DLC" or "Demo"
                             && (Location == "mlc" || Location.StartsWith("usb", StringComparison.Ordinal))
                             && Path?.StartsWith("/vol/storage_", StringComparison.Ordinal) != false
                             && Path?.Contains("hfio", StringComparison.Ordinal) != true;

    public string DisplayName => Name ?? "(no name)";

    /// <summary>Titles that misbehave when launched like a game; Launch asks first. The reason is shown.</summary>
    private static readonly Dictionary<string, string> Unstable = new()
    {
        // Homebrew Launcher for devkits (dev-signed). Seen on a CAT-R: the console ended up booting into it
        // as the master title; cos kill then killed the master and cos launch of it restarted Cafe.
        ["0005000013374842"] = "Homebrew Launcher for devkits is unstable on a CAT-R: it has ended up as the title the console " +
                               "boots into, and then kill and launch only restart the console back into it."
    };

    public string? LaunchWarning => Unstable.GetValueOrDefault(Id);

    /// <summary>Titles staged in the PCFS folder (data\mlc\usr\title\&lt;hi&gt;\&lt;lo&gt; with code\app.xml),
    /// named from their meta.xml. Read from this PC, not the console.</summary>
    public static List<TitleInfo> ScanPcfs(string? root)
    {
        var list = new List<TitleInfo>();
        var titles = root is null ? null : System.IO.Path.Combine(root, "data", "mlc", "usr", "title");
        if (titles is null || !Directory.Exists(titles))
            return list;
        try
        {
            foreach (var high in Directory.EnumerateDirectories(titles))
            foreach (var low in Directory.EnumerateDirectories(high))
            {
                var hi = System.IO.Path.GetFileName(high).ToLowerInvariant();
                var lo = System.IO.Path.GetFileName(low).ToLowerInvariant();
                if (hi.Length != 8 || lo.Length != 8 || !File.Exists(System.IO.Path.Combine(low, "code", "app.xml")))
                    continue;
                string? name = null;
                int? version = null;
                try
                {
                    var meta = File.ReadAllText(System.IO.Path.Combine(low, "meta", "meta.xml"));
                    name = XmlValue(meta, "longname_en") ?? XmlValue(meta, "shortname_en");
                    if (int.TryParse(XmlValue(meta, "title_version"), out var number))
                        version = number;
                }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
                {
                }
                string? appType = null;
                try
                {
                    appType = XmlValue(File.ReadAllText(System.IO.Path.Combine(low, "code", "app.xml")), "app_type")?.ToLowerInvariant();
                }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
                {
                }
                list.Add(new TitleInfo(hi + lo, name, version, "pcfs", null) { HostFolder = low, AppType = appType });
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
        }
        return list;
    }

    private static string? XmlValue(string xml, string tag)
    {
        var match = System.Text.RegularExpressions.Regex.Match(xml, $@"<{tag}\b[^>]*>([^<]*)</{tag}>");
        if (!match.Success) return null;
        var value = System.Net.WebUtility.HtmlDecode(match.Groups[1].Value).Replace("\r", "").Replace('\n', ' ').Trim();
        return value.Length > 0 ? value : null;
    }

    public static TitleInfo From(JsonElement item) => new(
        item.GetProperty("title").GetString() ?? "",
        item.TryGetProperty("name", out var name) && name.ValueKind == JsonValueKind.String ? name.GetString() : null,
        item.TryGetProperty("version", out var version) && version.ValueKind == JsonValueKind.Number ? version.GetInt32() : null,
        item.TryGetProperty("location", out var location) && location.ValueKind == JsonValueKind.String ? location.GetString() ?? "" : "",
        item.TryGetProperty("path", out var path) && path.ValueKind == JsonValueKind.String ? path.GetString() : null)
    {
        AppType = item.TryGetProperty("app_type", out var type) && type.ValueKind == JsonValueKind.String ? type.GetString() : null
    };
}

/// <summary>The last title list, kept in app data (%APPDATA%\catr\app\titles.json) so the page has data at start.</summary>
internal sealed class TitleCache
{
    public DateTime? Read { get; set; }
    public List<TitleInfo> Items { get; set; } = [];

    private static string FilePath => Path.Combine(AppSettings.Folder, "titles.json");

    public static TitleCache Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<TitleCache>(File.ReadAllText(FilePath)) ?? new();
        }
        catch (Exception exception) when (exception is IOException or JsonException)
        {
        }
        return new();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(AppSettings.Folder);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this));
        }
        catch (IOException)
        {
        }
    }
}
