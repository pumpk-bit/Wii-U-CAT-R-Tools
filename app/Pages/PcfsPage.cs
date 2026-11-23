using System.Text.Json;

namespace CatRTools;

/// <summary>PCFS: the host folder (catr's one saved folder, chosen in Settings), the server, and run.</summary>
internal sealed class PcfsPage : Page
{
    private readonly Label rootNote = Ui.Note("");
    private readonly CheckBox openLog = Ui.Check("Open the USB Log after launching");
    private readonly MetricCard server = new("SERVER", "—", "", 205);
    private readonly MetricCard connection = new("CONSOLE CONNECTION", "—", "", 205);
    private readonly MetricCard keeper = new("RECONNECT KEEPER", "—", "", 205);
    private readonly MetricCard boot = new("CONSOLE BOOT", "—", "from the serial log", 205);
    private readonly TextBox target = Ui.TextBox(placeholder: "Unpacked build folder (code/ content/ meta/), or a title ID already staged");
    private readonly CheckBox installed = Ui.Check("Installed title (PCFS only serves its writes)");
    private readonly CheckBox keepId = Ui.Check("Stage under the build's own ID");
    private readonly CheckBox debugFiles = Ui.Check("Include .elf/.map/.dla/.dnm");
    private readonly OutputPanel output = new("PCFS OUTPUT");
    private string? serverRoot;

    public PcfsPage(AppHost host) : base(host)
    {
        Name = "PCFS";
        // The host folder is chosen in Settings; here it is shown, with the server state.
        var serverCard = Ui.Card("Server", null, out var serverBody);
        rootNote.Margin = new Padding(0, 2, 0, 2);
        serverBody.Controls.Add(rootNote);
        serverBody.Controls.Add(Ui.Row(
            Ui.Link("Change folder in Settings", () => Host.ShowPage("Settings")),
            Ui.Link("Open folder", () => Ui.OpenFolder(this, EffectiveRoot))));
        var tiles = Ui.CardRow();
        tiles.BackColor = Theme.Surface;
        tiles.Controls.AddRange([server, connection, keeper, boot]);
        serverBody.Controls.Add(tiles);
        serverBody.Controls.Add(Ui.Row(
            Ui.Button("Refresh", async (_, _) => await RefreshStatus()),
            Ui.Button("Start (pcfs up)…", async (_, _) => await Up(), primary: true),
            Ui.Button("Stop server", async (_, _) => await Down()),
            Ui.Button("Start keeper", async (_, _) => await Keep(true)),
            Ui.Button("Stop keeper", async (_, _) => await Keep(false))));
        serverBody.Controls.Add(Ui.Note("pcfs up restarts the console into the Recovery Menu until PCFS mounts (about half of boots fail TCP init; up to 3 tries). The keeper reconnects the server after every restart."));

        target.Text = host.Settings.LastRunTarget ?? "";
        var runCard = Ui.Card("Run a build over PCFS", null, out var runBody);
        runBody.Controls.Add(Ui.PathRow(target,
            Ui.Button("Folder…", (_, _) =>
            {
                if (Ui.PickFolder(this, "Choose an unpacked build folder", Directory.Exists(target.Text) ? target.Text : null) is { } folder)
                    target.Text = folder;
            })));
        openLog.Checked = host.Settings.OpenLogAfterRun;
        openLog.CheckedChanged += (_, _) => { Host.Settings.OpenLogAfterRun = openLog.Checked; Host.Settings.Save(); };
        runBody.Controls.Add(Ui.Row(installed, keepId, debugFiles));
        runBody.Controls.Add(Ui.Row(openLog));
        runBody.Controls.Add(Ui.Row(
            Ui.Button("Stage and launch", async (_, _) => await Run(), primary: true),
            Ui.Button("Back to Recovery Menu…", async (_, _) => await Stop()),
            Ui.Button("USB Log", (_, _) => Host.ShowLog())));

        Controls.Add(Ui.PageLayout(serverCard, runCard, output));
        DescribeRoot();
    }

    public override string Title => "PCFS";
    public override string Subtitle => "PC file system for the console: host folder, server, and running builds from the PC.";

    private bool checkedOnce;

    public override async void Shown()
    {
        DescribeRoot();   // the folder may have changed in Settings
        if (!checkedOnce)
        {
            checkedOnce = true;
            await RefreshStatus(quiet: true);
        }
    }

    private string? EffectiveRoot => CatrLauncher.PcfsRoot(Host.Settings);

    private void DescribeRoot()
    {
        var path = EffectiveRoot;
        const string label = "Host folder";
        if (path is null || !Directory.Exists(path))
            (rootNote.Text, rootNote.ForeColor) = ($"{label}: {path ?? "?"} — not found. The PCFS server needs an existing folder.", Theme.Bad);
        else if (serverRoot is not null && !SamePath(serverRoot, path))
            (rootNote.Text, rootNote.ForeColor) = ($"{label}: {path}. The running server still serves {serverRoot}; Stop it, then Start.", Theme.Warn);
        else
            (rootNote.Text, rootNote.ForeColor) = ($"{label}: {path}", Theme.Ink);
    }

    private static bool SamePath(string a, string b) =>
        string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);

    // catr serves its one saved folder (catr pcfs root); nothing to pass.
    private static List<string> WithRoot(params string[] args) => args.ToList();

    private bool RootExists()
    {
        if (EffectiveRoot is { } path && Directory.Exists(path)) return true;
        Ui.Error(this, $"The PCFS folder doesn't exist:\n{EffectiveRoot}");
        return false;
    }

    private async Task RefreshStatus(bool quiet = false)
    {
        var result = await Host.Run(["pcfs", "status"], quiet ? null : output.Box, quiet: true);
        if (result?.Json() is not { } state) return;
        if (state.GetProperty("server") is { ValueKind: JsonValueKind.Object } running)
        {
            serverRoot = running.TryGetProperty("root", out var r) ? r.GetString() : null;
            server.Set("Running", $"PID {Field(running, "pid")} · {serverRoot}", Theme.Good);
            var tcp = state.TryGetProperty("tcp", out var t) ? t.GetString() : null;
            connection.Set(tcp ?? "—", tcp == "ESTABLISHED" ? "console connected" : "waiting for the console", tcp == "ESTABLISHED" ? Theme.Good : Theme.Warn);
        }
        else
        {
            serverRoot = null;
            server.Set("Not running", "");
            connection.Set("—", "");
        }
        if (state.GetProperty("keeper") is { ValueKind: JsonValueKind.Object } keep)
            keeper.Set("Running", $"PID {Field(keep, "pid")} · since {Field(keep, "started")}", Theme.Good);
        else
            keeper.Set("Not running", "");
        if (state.TryGetProperty("console_boot", out var console))
        {
            if (console.ValueKind == JsonValueKind.Object)
                boot.Set(Field(console, "state"), Field(console, "detail"));
            else
                boot.Set("Unavailable", console.GetString() ?? "");
        }
        else
        {
            boot.Set("—", "serial service not running");
        }
        DescribeRoot();
    }

    private static string Field(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) ? value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : value.ToString() : "";

    private async Task Up()
    {
        if (!RootExists()) return;
        var args = WithRoot("pcfs", "up");
        if (serverRoot is not null && !SamePath(serverRoot, EffectiveRoot!))
            args.Add("--force");
        await Host.Run(args, output.Box,
            confirm: $"Start PCFS for {EffectiveRoot}?\n\nThis restarts the console into the Recovery Menu (up to 3 times) until PCFS mounts.");
        await RefreshStatus(quiet: true);
    }

    private async Task Down()
    {
        await Host.Run(["pcfs", "down"], output.Box, confirm: "Stop catr's PCFS server (and the background keeper)?\n\nA title using PCFS loses its files.");
        await RefreshStatus(quiet: true);
    }

    private async Task Keep(bool start)
    {
        if (start && !RootExists()) return;
        await Host.Run(start ? WithRoot("pcfs", "keep", "--background") : ["pcfs", "keep", "--stop"], output.Box);
        await RefreshStatus(quiet: true);
    }

    private async Task Run()
    {
        var what = target.Text.Trim();
        if (what.Length == 0)
        {
            Ui.Error(this, "Choose a build folder or type a staged title ID.");
            return;
        }
        if (!RootExists()) return;
        Host.Settings.LastRunTarget = what;
        var args = WithRoot("run", what, "--no-follow");
        if (installed.Checked) args.Add("--installed");
        if (keepId.Checked) args.Add("--keep-title-id");
        if (debugFiles.Checked) args.Add("--include-debug");
        // No dialog: the button says what it does. catr restarts the console only if PCFS isn't mounted.
        output.Box.Line($"Staging and launching {what} (PCFS folder {EffectiveRoot})", Theme.TerminalDim);
        var result = await Host.Run(args, output.Box, yes: true);
        if (result?.Ok == true && openLog.Checked)
            Host.ShowLog();
        await RefreshStatus(quiet: true);
    }

    private async Task Stop()
    {
        await Host.Run(["stop"], output.Box, confirm: "Return the console to the Recovery Menu and stop the PCFS server?");
        await RefreshStatus(quiet: true);
    }
}
