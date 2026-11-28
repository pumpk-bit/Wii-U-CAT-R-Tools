using System.Text.Json;

namespace CatRTools;

internal sealed class SettingsPage : Page
{
    private readonly TextBox ip = Ui.TextBox(placeholder: "e.g. 192.0.2.50; empty searches this PC's network");
    private readonly TextBox port = Ui.TextBox(placeholder: "e.g. COM3 (Device Manager > Ports)");
    private readonly Label consoleNote = Ui.Note("");
    private readonly OutputPanel output = new();
    private readonly TextBox pcfsRoot = Ui.TextBox();
    private readonly Label pcfsNote = Ui.Note("");
    private readonly ComboBox theme = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 140, Font = Theme.Small, Margin = new Padding(0, 6, 12, 4) };
    private readonly Label themeNote = new() { AutoSize = true, Font = Theme.Small, ForeColor = Theme.Warn, Margin = new Padding(0, 9, 0, 0) };

    private string? EffectivePcfs => string.IsNullOrWhiteSpace(pcfsRoot.Text)
        ? CatrLauncher.PcfsRoot(Host.Settings) : pcfsRoot.Text.Trim();

    private bool savingPcfs;

    /// <summary>Saves the folder in catr's config (catr pcfs root), the one place every catr command reads it.</summary>
    private async Task SavePcfs()
    {
        if (savingPcfs || string.IsNullOrWhiteSpace(pcfsRoot.Text)) { ShowSavedPcfs(); return; }
        var value = Path.GetFullPath(pcfsRoot.Text.Trim());
        if (CatrLauncher.PcfsRoot(Host.Settings) is { } current &&
            string.Equals(Path.GetFullPath(current).TrimEnd('\\'), value.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            return;
        savingPcfs = true;
        try
        {
            var result = await Host.Run(["pcfs", "root", value], output.Box);
            if (result?.Ok != true)
                Ui.Error(this, "catr kept the old PCFS folder; see the output below.\n(A running PCFS server or keeper has to be stopped first.)");
        }
        finally
        {
            savingPcfs = false;
        }
        ShowSavedPcfs();
        Host.NotifyTitles();   // the Titles page lists the folder's staged titles
    }

    private void ShowSavedPcfs()
    {
        pcfsRoot.Text = CatrLauncher.PcfsRoot(Host.Settings) ?? "";
        DescribePcfs();
    }

    private void DescribePcfs()
    {
        var path = EffectivePcfs;
        (pcfsNote.Text, pcfsNote.ForeColor) = path is null || !Directory.Exists(path)
            ? ($"{path ?? "No folder"}: not found. The PCFS server needs an existing folder.", Theme.Bad)
            : ($"Every catr command serves this folder (catr pcfs root): {path}", Theme.Muted);
    }

    public SettingsPage(AppHost host) : base(host)
    {
        Name = "Settings";
        var console = Ui.Card("Console", $"Kept in catr's config ({CatrLauncher.ConfigFile}). Find checks that wupserver answers at the IP, then saves it with the serial port.", out var consoleBody);
        consoleBody.Controls.Add(Ui.Caption("CONSOLE IP"));
        consoleBody.Controls.Add(Ui.PathRow(ip));
        consoleBody.Controls.Add(Ui.Caption("USB SERIAL PORT"));
        consoleBody.Controls.Add(Ui.PathRow(port));
        consoleBody.Controls.Add(consoleNote);
        consoleBody.Controls.Add(Ui.Row(Ui.Button("Find and save", async (_, _) => await Find(), primary: true)));

        pcfsRoot.Text = CatrLauncher.PcfsRoot(host.Settings) ?? "";
        pcfsRoot.PlaceholderText = $"catr's default: {CatrLauncher.DefaultPcfsRoot(host.Settings) ?? "PCFS"}";
        pcfsRoot.TextChanged += (_, _) => DescribePcfs();
        pcfsRoot.Leave += async (_, _) => await SavePcfs();
        var pcfs = Ui.Card("PCFS host folder", "The one PC folder the console sees over PCFS. Saved in catr's config, so pcfs up, pcfs keep, run and Spark captures all use it; catr refuses any other folder.", out var pcfsBody);
        pcfsBody.Controls.Add(Ui.PathRow(pcfsRoot,
            Ui.Button("Browse…", async (_, _) =>
            {
                if (Ui.PickFolder(this, "Choose the PCFS host folder", EffectivePcfs) is { } folder)
                {
                    pcfsRoot.Text = folder;
                    await SavePcfs();
                }
            }),
            Ui.Button("Default", async (_, _) => { pcfsRoot.Text = CatrLauncher.DefaultPcfsRoot(Host.Settings) ?? ""; await SavePcfs(); }),
            Ui.Button("Open", (_, _) => Ui.OpenFolder(this, EffectivePcfs))));
        pcfsBody.Controls.Add(pcfsNote);

        var appearance = Ui.Card("Window", null, out var appearanceBody);
        theme.Items.AddRange(["System", "Light", "Dark"]);
        theme.SelectedItem = host.Settings.Theme is "Light" or "Dark" ? host.Settings.Theme : "System";
        theme.SelectedIndexChanged += (_, _) =>
        {
            Host.Settings.Theme = (string)theme.SelectedItem!;
            Host.Settings.Save();
            themeNote.Text = "Restart CatR-Tools to apply.";
        };
        appearanceBody.Controls.Add(Ui.Row(new Label { Text = "Theme", AutoSize = true, Font = Theme.Small, ForeColor = Theme.Ink, Margin = new Padding(0, 9, 10, 0) }, theme, themeNote));
        var onClose = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 260, Font = Theme.Small, Margin = new Padding(0, 6, 12, 4) };
        string[] closeModes = ["Ask", "Stop", "Leave"];
        onClose.Items.AddRange(["Ask what to do", "Stop the background services", "Leave them running"]);
        onClose.SelectedIndex = Math.Max(0, Array.IndexOf(closeModes, host.Settings.OnClose));
        onClose.SelectedIndexChanged += (_, _) => { Host.Settings.OnClose = closeModes[onClose.SelectedIndex]; Host.Settings.Save(); };
        appearanceBody.Controls.Add(Ui.Row(new Label { Text = "On close", AutoSize = true, Font = Theme.Small, ForeColor = Theme.Ink, Margin = new Padding(0, 9, 10, 0) }, onClose));
        appearanceBody.Controls.Add(Ui.Note("Background services: the PCFS keeper, the PCFS server and the serial service (COM port)."));
        appearanceBody.Controls.Add(Ui.Note($"Window settings and history: {AppSettings.FilePath} (File > Open app data)"));

        var titles = Ui.Card("Titles", "What the Titles page lists. SLC, GamePad (DRH) and Bluetooth titles are never listed.", out var titlesBody);
        var system = Ui.Check("Show system titles (system apps, system data, applets)", host.Settings.ShowSystemTitles);
        system.CheckedChanged += (_, _) => { Host.Settings.ShowSystemTitles = system.Checked; Host.Settings.Save(); Host.NotifyTitles(); };
        var unnamed = Ui.Check("Hide titles without a name", host.Settings.HideUnnamedTitles);
        unnamed.CheckedChanged += (_, _) => { Host.Settings.HideUnnamedTitles = unnamed.Checked; Host.Settings.Save(); Host.NotifyTitles(); };
        titlesBody.Controls.Add(system);
        titlesBody.Controls.Add(Ui.Note("Menus (app type MENU, as System Config Tool shows it: Wii U Menu, CAT Recovery Menu, Kiosk Menu, System Config Tool) are listed either way."));
        titlesBody.Controls.Add(unnamed);
        var removal = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 200, Font = Theme.Small, Margin = new Padding(0, 6, 12, 4) };
        string[] modes = ["Trash", "Delete", "Ask"];
        removal.Items.AddRange(["Move to the Recycle Bin", "Delete permanently", "Ask each time"]);
        removal.SelectedIndex = Math.Max(0, Array.IndexOf(modes, host.Settings.PcfsRemoval));
        removal.SelectedIndexChanged += (_, _) => { Host.Settings.PcfsRemoval = modes[removal.SelectedIndex]; Host.Settings.Save(); };
        titlesBody.Controls.Add(Ui.Row(new Label { Text = "Removing a PCFS title", AutoSize = true, Font = Theme.Small, ForeColor = Theme.Ink, Margin = new Padding(0, 9, 10, 0) }, removal));

        Controls.Add(Ui.PageLayout(console, pcfs, titles, appearance, output));
        DescribePcfs();
        LoadConsole();
    }

    public override string Title => "Settings";
    public override string Subtitle => "The console catr talks to, the PCFS folder, the Titles list and the window.";

    public override void Shown() => LoadConsole();

    private void LoadConsole()
    {
        var config = AppHost.CatrConfig();
        if (config is not { } values) { consoleNote.Text = "No console saved yet."; return; }
        if (ip.Text.Length == 0 && values.TryGetProperty("ip", out var address)) ip.Text = address.GetString();
        if (port.Text.Length == 0 && values.TryGetProperty("serial_port", out var serial)) port.Text = serial.GetString();
        consoleNote.Text = $"Saved: {(values.TryGetProperty("ip", out var a) ? a.GetString() : "no IP")}, {(values.TryGetProperty("serial_port", out var p) ? p.GetString() : "no COM port")}" +
                           (values.TryGetProperty("baud", out var baud) ? $" at {baud} baud" : "");
    }

    private async Task Find()
    {
        var address = ip.Text.Trim();
        var args = new List<string> { "find" };     // without an IP, catr searches this PC's networks
        if (address.Length > 0)
            args.AddRange(["--ip", address]);
        if (port.Text.Trim() is { Length: > 0 } com)
            args.AddRange(["--port", com]);
        var result = await Host.Run(args, output.Box);
        ip.Clear();
        port.Clear();
        LoadConsole();
        (FindForm() as MainForm)?.UpdateConsolePill();
        if (result?.Ok == false)
            consoleNote.Text += " · find failed, see output";
    }
}
