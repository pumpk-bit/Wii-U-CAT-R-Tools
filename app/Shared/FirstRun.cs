using System.Text.Json.Nodes;

namespace CatRTools;

/// <summary>First start of a CatR-Tools.exe that keeps its settings next to itself (CatrLauncher.Portable):
/// config.json is created there, seeded from catr's %APPDATA%\catr\config.json, and a PCFS folder is
/// created there too, filled from the Cafe SDK's data folder when there is one.</summary>
internal static class FirstRun
{
    public static string PcfsFolder => Path.Combine(CatrLauncher.AppFolder, "PCFS");
    static string PartialFolder => PcfsFolder + ".partial";

    /// <summary>The SDK's data subfolders the PCFS host folder starts from.</summary>
    static readonly string[] SdkData = ["disc", "mlc", "slc"];

    /// <summary>catr's own %APPDATA%\catr\config.json.</summary>
    static string HomeConfig => Path.Combine(CatrLauncher.CatrHome, "config.json");

    /// <summary>Creates config.json next to the exe if it isn't there, with the console IP, serial port,
    /// workspace and the rest copied from the settings catr.cmd uses now (what catr's own config.json
    /// points to, else that file). pcfs_root is not copied: the PCFS folder next to the exe is set up
    /// instead (SetUpPcfs). Then points catr's own config.json at it (PointCatrAtConfig).</summary>
    public static void SeedConfig()
    {
        if (!CatrLauncher.Portable)
            return;
        if (!File.Exists(CatrLauncher.ConfigFile))
        {
            var values = new JsonObject();
            var source = ReadObject(HomeConfig) is { } home && Text(home, "config_file") is { } pointed && File.Exists(pointed)
                ? pointed : HomeConfig;
            if (ReadObject(source) is { } saved)
                foreach (var (key, value) in saved)
                    if (key is not ("pcfs_root" or "config_file"))
                        values[key] = value?.DeepClone();
            try
            {
                CatrLauncher.WriteJson(CatrLauncher.ConfigFile, values);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                return;
            }
        }
        PointCatrAtConfig();
    }

    /// <summary>Sets "config_file" in catr's own config.json to this exe's config.json, so catr.cmd reads
    /// and saves the same settings (catr's config.config_file). The CatR-Tools.exe started last wins.
    /// A config.json that can't be read is left alone rather than replaced.</summary>
    static void PointCatrAtConfig()
    {
        JsonObject home;
        if (!File.Exists(HomeConfig))
            home = [];
        else if (ReadObject(HomeConfig) is { } existing)
            home = existing;
        else
            return;
        if (Text(home, "config_file") == CatrLauncher.ConfigFile)
            return;
        try
        {
            // Settings catr.cmd had that this config.json lacks (a workspace saved later, say) come
            // along, so catr.cmd keeps working the same once it follows the pointer.
            var mine = Read();
            var added = false;
            foreach (var (key, value) in home)
                if (key is not ("pcfs_root" or "config_file") && !mine.ContainsKey(key))
                {
                    mine[key] = value?.DeepClone();
                    added = true;
                }
            if (added)
                CatrLauncher.WriteJson(CatrLauncher.ConfigFile, mine);
            home["config_file"] = CatrLauncher.ConfigFile;
            CatrLauncher.WriteJson(HomeConfig, home);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            // catr.cmd keeps its own settings until the next start.
        }
    }

    /// <summary>Window start: when catr has no PCFS folder set yet (or it is this exe's own, deleted),
    /// use (or make) the one next to the exe. A folder the user chose is never replaced, even when it is
    /// missing right now (an unplugged drive): that only gets a warning.</summary>
    public static void SetUpPcfs(IWin32Window owner)
    {
        if (!CatrLauncher.Portable || AppHost.SnapshotMode)
            return;
        var config = Read();
        var root = Text(config, "pcfs_root");
        if (root is not null && !SameFolder(root, PcfsFolder))
        {
            if (!Directory.Exists(root))
                Ui.Error(owner, $"The PCFS folder {root} isn't there right now (an unplugged drive, or a network " +
                    "share that isn't connected?).\n\nIt is still the PCFS folder. Connect it again, or choose another on the PCFS page.");
            return;
        }
        if (root is not null && Directory.Exists(PcfsFolder))
            return;
        try
        {
            if (!Directory.Exists(PcfsFolder))
            {
                var sdk = SdkDataFolder();
                if (sdk is not null)
                {
                    using var dialog = new PcfsCopyForm(sdk, PartialFolder);
                    if (dialog.ShowDialog(owner) != DialogResult.OK)
                    {
                        if (dialog.Failure is { } failure)
                            Ui.Error(owner, $"The PCFS folder could not be filled from the Cafe SDK:\n\n{failure}\n\n" +
                                "What was copied is kept in PCFS.partial; the copy carries on at the next start.");
                        return;     // cancelled or failed: catr keeps its default folder until next time
                    }
                }
                else if (Directory.Exists(PartialFolder))
                {
                    // An unfinished copy, and no SDK to finish it from: don't pass it off as complete.
                    Ui.Error(owner, $"{PartialFolder} holds an unfinished copy of the Cafe SDK's PCFS files, but " +
                        "the Cafe SDK isn't found now, so it can't be finished.\n\nSet CAFE_ROOT and start CatR-Tools again, " +
                        "or delete PCFS.partial to start with an empty PCFS folder.");
                    return;
                }
                CreateLayout(PartialFolder);
                Directory.Move(PartialFolder, PcfsFolder);
            }
            config = Read();
            config["pcfs_root"] = PcfsFolder;
            CatrLauncher.WriteJson(CatrLauncher.ConfigFile, config);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            Ui.Error(owner, $"Could not set up the PCFS folder {PcfsFolder}:\n\n{exception.Message}");
        }
    }

    static bool SameFolder(string a, string b)
    {
        try
        {
            return string.Equals(Path.TrimEndingDirectorySeparator(Path.GetFullPath(a)),
                Path.TrimEndingDirectorySeparator(Path.GetFullPath(b)), StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return false;
        }
    }

    /// <summary>A non-empty string value, or null (missing, or a number or anything else hand-edited in).</summary>
    static string? Text(JsonObject values, string key) =>
        values[key] is JsonValue value && value.TryGetValue<string>(out var text) && text.Length > 0 ? text : null;

    /// <summary>The file's JSON object, or null when it is missing, unreadable or not an object.</summary>
    static JsonObject? ReadObject(string path)
    {
        try
        {
            return File.Exists(path) ? JsonNode.Parse(File.ReadAllText(path)) as JsonObject : null;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Text.Json.JsonException)
        {
            return null;
        }
    }

    /// <summary>The Cafe SDK's data folder, when there is one: "cafe_root" in config.json, else CAFE_ROOT
    /// (as catr's config.cafe_roots; CAFE_ROOT may be C:\x, /c/x or /cygdrive/c/x).</summary>
    static string? SdkDataFolder()
    {
        var cafe = Text(Read(), "cafe_root") ?? Environment.GetEnvironmentVariable("CAFE_ROOT");
        if (string.IsNullOrEmpty(cafe))
            return null;
        var unix = System.Text.RegularExpressions.Regex.Match(cafe, "^(?:/cygdrive)?/([A-Za-z])(?:/(.*))?$");
        if (unix.Success)
            cafe = $"{unix.Groups[1].Value.ToUpperInvariant()}:\\{unix.Groups[2].Value.Replace('/', '\\')}";
        var data = Path.Combine(cafe, "data");
        return Directory.Exists(data) ? data : null;
    }

    /// <summary>The folders a PCFS host folder has : data\{disc,mlc,slc,save}, overlays, temp.</summary>
    static void CreateLayout(string folder)
    {
        foreach (var part in new[] { @"data\disc", @"data\mlc", @"data\slc", @"data\save", "overlays", "temp" })
            Directory.CreateDirectory(Path.Combine(folder, part));
    }

    /// <summary>Copies the SDK's data\{disc,mlc,slc} into target\data. Files already there with the same
    /// size are skipped, so a cancelled copy carries on where it stopped.</summary>
    public static void CopySdkData(string sdkData, string target, IProgress<(long Done, long Total, string File)> progress,
        CancellationToken cancel)
    {
        var parts = SdkData.Select(part => Path.Combine(sdkData, part)).Where(Directory.Exists).ToList();
        // Empty folders too (title content\ and meta\ folders, for one), not only the ones with files.
        foreach (var folder in parts.SelectMany(part => Directory.EnumerateDirectories(part, "*", SearchOption.AllDirectories).Prepend(part)))
            Directory.CreateDirectory(Path.Combine(target, "data", Path.GetRelativePath(sdkData, folder)));
        var files = parts.SelectMany(part => Directory.EnumerateFiles(part, "*", SearchOption.AllDirectories))
            .Select(path => new FileInfo(path)).ToList();
        var total = files.Sum(file => file.Length);
        long done = 0;
        foreach (var file in files)
        {
            cancel.ThrowIfCancellationRequested();
            var relative = Path.GetRelativePath(sdkData, file.FullName);
            var destination = new FileInfo(Path.Combine(target, "data", relative));
            progress.Report((done, total, relative));
            if (!destination.Exists || destination.Length != file.Length)
            {
                Directory.CreateDirectory(destination.DirectoryName!);
                var temporary = destination.FullName + ".copying";
                File.Copy(file.FullName, temporary, overwrite: true);
                File.Move(temporary, destination.FullName, overwrite: true);
            }
            done += file.Length;
        }
        progress.Report((total, total, ""));
    }

    static JsonObject Read() => ReadObject(CatrLauncher.ConfigFile) ?? [];
}

/// <summary>The first-start copy of the Cafe SDK's PCFS data, with progress and Cancel.</summary>
internal sealed class PcfsCopyForm : Form
{
    private readonly CancellationTokenSource cancel = new();
    private bool copying;
    private readonly ProgressBar bar = new() { Dock = DockStyle.Fill, Maximum = 1000, Height = 18 };
    private readonly Label file = new() { AutoSize = true, Dock = DockStyle.Fill, AutoEllipsis = true };

    public string? Failure { get; private set; }

    public PcfsCopyForm(string sdkData, string target)
    {
        Text = "Setting up PCFS";
        StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = MaximizeBox = ControlBox = false;
        ClientSize = new Size(560, 190);
        BackColor = Theme.Surface;
        Font = Theme.Body;
        var layout = Ui.Column(new Padding(18));
        layout.Dock = DockStyle.Fill;
        layout.Controls.Add(new Label
        {
            Text = $"Creating the PCFS folder next to CatR-Tools.exe from the Cafe SDK ({sdkData}).",
            AutoSize = true, Dock = DockStyle.Fill, Font = Theme.BodyBold, ForeColor = Theme.Ink
        });
        layout.Controls.Add(bar);
        file.ForeColor = Theme.Muted;
        layout.Controls.Add(file);
        var stop = Ui.Button("Cancel", (_, _) => { cancel.Cancel(); file.Text = "Stopping…"; });
        layout.Controls.Add(Ui.Row(stop));
        Controls.Add(layout);
        Theme.Style(this);
        Theme.TitleBar(this);

        // Closing the window any other way (Alt+F4) stops the copy first instead of leaving it running.
        FormClosing += (_, e) =>
        {
            if (!copying || e.CloseReason != CloseReason.UserClosing)
                return;
            e.Cancel = true;
            cancel.Cancel();
            file.Text = "Stopping…";
        };
        Shown += async (_, _) =>
        {
            copying = true;
            var progress = new Progress<(long Done, long Total, string File)>(step =>
            {
                bar.Value = step.Total == 0 ? 1000 : (int)(step.Done * 1000 / step.Total);
                file.Text = $"{step.Done / 1048576.0:0} of {step.Total / 1048576.0:0} MB   {step.File}";
            });
            try
            {
                await Task.Run(() => FirstRun.CopySdkData(sdkData, target, progress, cancel.Token));
                DialogResult = DialogResult.OK;
            }
            catch (OperationCanceledException)
            {
                DialogResult = DialogResult.Cancel;
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                Failure = exception.Message;
                DialogResult = DialogResult.Abort;
            }
            copying = false;
            Close();
        };
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
            cancel.Dispose();
        base.Dispose(disposing);
    }
}
