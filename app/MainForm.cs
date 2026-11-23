namespace CatRTools;

/// <summary>A sidebar page. Built once; ShowPage only switches which one is visible.</summary>
internal abstract class Page : Panel
{
    protected Page(AppHost host)
    {
        Host = host;
        Dock = DockStyle.Fill;
        BackColor = Theme.Canvas;
        Visible = false;
    }

    protected AppHost Host { get; }
    public abstract string Title { get; }
    public abstract string Subtitle { get; }

    /// <summary>Called each time the page is shown.</summary>
    public virtual void Shown() { }
}

internal sealed class MainForm : Form
{
    private readonly Panel content = new() { Dock = DockStyle.Fill, BackColor = Theme.Canvas };
    private readonly Label pageTitle = new() { AutoSize = true, Font = Theme.PageTitle, ForeColor = Theme.Ink, Location = new Point(0, 14) };
    private readonly Label pageSubtitle = new() { AutoSize = true, Font = Theme.Small, ForeColor = Theme.Muted, Location = new Point(2, 50) };
    private readonly Label consolePill = new()
    {
        AutoSize = true, Anchor = AnchorStyles.Top | AnchorStyles.Right, BackColor = Theme.Field,
        ForeColor = Theme.Slate, Font = Theme.Caption, Padding = new Padding(11, 8, 11, 8)
    };
    private readonly ToolStripStatusLabel statusText = new() { Spring = true, TextAlign = ContentAlignment.MiddleLeft };
    private readonly ToolStripStatusLabel runningText = new() { ForeColor = Theme.Accent };
    private readonly ToolStripButton stopButton = new("Stop") { Enabled = false, ToolTipText = "Stop the running catr command" };
    private readonly Dictionary<string, Label> navButtons = [];
    private readonly Dictionary<string, Page> pages = [];
    private readonly AppHost host;
    private LogForm? logWindow;
    private Page? current;

    public MainForm(AppSettings settings)
    {
        Text = "CatR-Tools";
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(940, 660);
        Size = new Size(1220, 820);
        BackColor = Theme.Canvas;
        Font = Theme.Body;

        host = new AppHost(settings) { Owner = this, ShowLog = ShowLogWindow, ShowPage = ShowPage, SetStatus = text => statusText.Text = text };

        var menu = new MenuStrip { BackColor = Theme.Surface, Padding = new Padding(10, 5, 0, 5) };
        var file = new ToolStripMenuItem("File");
        file.DropDownItems.Add(new ToolStripMenuItem("Open CatR-Tools folder", null,
            (_, _) => Ui.OpenFolder(this, CatrLauncher.AppFolder)) { ToolTipText = "CatR-Tools.exe, config.json and the PCFS folder" });
        file.DropDownItems.Add(new ToolStripMenuItem("Open app data", null, (_, _) =>
        {
            Directory.CreateDirectory(CatrLauncher.CatrHome);
            Ui.OpenFolder(this, CatrLauncher.CatrHome);
        }) { ToolTipText = "Caches and saved data: icons, title names, window settings, service state, PCFS server logs" });
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add("Exit", null, (_, _) => Close());
        var view = new ToolStripMenuItem("View");
        view.DropDownItems.Add("USB Log", null, (_, _) => ShowLogWindow());
        var help = new ToolStripMenuItem("Help");
        help.DropDownItems.Add(new ToolStripMenuItem("Documentation", null, (_, _) => HelpForm.Open(this))
            { ShortcutKeys = Keys.F1 });
        help.DropDownItems.Add("PCFS: offline or online", null, (_, _) => HelpForm.Open(this, "PCFS"));
        help.DropDownItems.Add("Command line", null, (_, _) => HelpForm.Open(this, "Command line"));
        help.DropDownItems.Add("catr commands", null, (_, _) => HelpForm.Open(this, "catr commands"));
        help.DropDownItems.Add(new ToolStripSeparator());
        help.DropDownItems.Add("About", null, (_, _) => MessageBox.Show(this,
            "CatR-Tools\nA desktop front end for catr, with catr, Python and pcfsserver.exe built in.\n\n" +
            $"catr: {(CatrLauncher.UsesBundle(settings) ? "built in" : CatrLauncher.FindCatrFolder(settings) ?? "not found")}\n" +
            $"Python: {CatrLauncher.PythonDescription(settings)}\n" +
            $"Workspace: {CatrLauncher.Workspace(settings) ?? "not found"}\n" +
            $"Config: {CatrLauncher.ConfigFile}\n" +
            $"App data: {CatrLauncher.CatrHome}\n\n" +
            "wupclient from iosuhax by smealum (github.com/smealum/iosuhax).\n" +
            "pcfsserver from sprig by Cynthia Coan (codeberg.org/rem-verse/sprig), MIT license, with local changes.\n" +
            "Free, non-commercial use only.",
            "About CatR-Tools", MessageBoxButtons.OK, MessageBoxIcon.Information));
        menu.Items.AddRange([file, view, help]);
        MainMenuStrip = menu;

        var status = new StatusStrip { BackColor = Theme.Surface, SizingGrip = true };
        stopButton.Click += (_, _) => StopRunning();
        status.Items.AddRange([statusText, runningText, stopButton]);
        host.Runner.RunningChanged += () =>
        {
            var running = host.Runner.Running;
            runningText.Text = running is null ? "" : $"● {running}";
            stopButton.Enabled = running is not null;
            UseWaitCursor = false;
        };

        var shell = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, BackColor = Theme.Canvas, Margin = Padding.Empty };
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 224));
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        shell.Controls.Add(BuildSidebar(), 0, 0);
        shell.Controls.Add(BuildMainArea(), 1, 0);
        Controls.Add(shell);
        Controls.Add(status);
        Controls.Add(menu);

        foreach (var page in new Page[]
                 {
                     new OverviewPage(host), new TitlesPage(host), new InstallPage(host), new CommandsPage(host),
                     new PcfsPage(host), new DiagnosticsPage(host), new SettingsPage(host)
                 })
        {
            pages[page.Name] = page;
            content.Controls.Add(page);
        }
        UpdateConsolePill();
        statusText.Text = CatrLauncher.UsesBundle(settings) ? $"catr built in ({CatrLauncher.PythonDescription(settings)})"
            : CatrLauncher.FindCatrFolder(settings) is { } folder ? $"catr: {folder}"
            : Bundle.Error ?? "catr.py not found: build CatR-Tools.exe with build.ps1";
        Theme.Style(this);
        Theme.TitleBar(this);
        FormClosing += OnClosing;
        Load += (_, _) => ShowPage("Overview");
        Shown += (_, _) => FirstRun.SetUpPcfs(this);   // first start: the PCFS folder next to the exe
    }

    public void UpdateConsolePill()
    {
        var config = AppHost.CatrConfig();
        var ip = config?.TryGetProperty("ip", out var value) == true ? value.GetString() : null;
        consolePill.Text = ip is null ? "●  NO CONSOLE SELECTED" : $"●  CAT-R  ·  {ip}";
        consolePill.Left = Math.Max(8, consolePill.Parent?.ClientSize.Width - consolePill.Width ?? 0);
    }

    private void StopRunning()
    {
        var running = host.Runner.Running;
        if (running is null)
            return;
        var follow = running.Contains("--follow") || running.StartsWith("catr log", StringComparison.Ordinal);
        if (!follow && !Ui.Confirm(this,
                $"Stop {running}?\n\nStopping a command in the middle of a console transfer can leave IOSU file handles " +
                "open until the next restart (catr reboot frees them). A partly copied staging folder is left in usr/tmp.",
                "Stop catr"))
            return;
        host.Runner.Stop();
    }

    private bool backgroundStopped;

    /// <summary>catr's background helpers that outlive this window: the PCFS keeper, PCFS servers
    /// (any pcfsserver.exe) and the serial service.</summary>
    private static List<string> BackgroundServices()
    {
        var found = new List<string>();
        try
        {
            using var keep = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(AppHost.CatrHome, "pcfs-keep.json")));
            if (keep.RootElement.TryGetProperty("pid", out var pid) && pid.TryGetInt32(out var id) && Alive(id))
                found.Add($"PCFS keeper (PID {id})");
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Text.Json.JsonException)
        {
        }
        foreach (var server in System.Diagnostics.Process.GetProcessesByName("pcfsserver"))
            using (server)
                found.Add($"PCFS server (PID {server.Id})");
        if (File.Exists(Path.Combine(AppHost.CatrHome, "serial.json")))
            found.Add("serial service (holds the USB serial port)");
        return found;
    }

    private static bool Alive(int pid)
    {
        try
        {
            using var process = System.Diagnostics.Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false;
        }
    }

    /// <summary>Ask (or follow the saved choice) whether to stop catr's background services too.
    /// null: cancel closing.</summary>
    private bool? AskStopBackground(List<string> services, string? running)
    {
        switch (host.Settings.OnClose)
        {
            case "Stop": return true;
            case "Leave": return false;
        }
        var stop = new TaskDialogButton("Stop all and close");
        var leave = new TaskDialogButton("Leave running and close");
        var page = new TaskDialogPage
        {
            Caption = "Close CatR-Tools",
            Heading = "Stop catr's background services too?",
            Text = "Still running:\n• " + string.Join("\n• ", services) +
                   (running is null ? "" : $"\n\n{running} is also running; it is stopped either way.") +
                   "\n\nStopping the PCFS server while a title reads from PCFS hangs that title.",
            Verification = new TaskDialogVerificationCheckBox("Don't ask again (change it in Settings)"),
            DefaultButton = stop
        };
        page.Buttons.Add(stop);
        page.Buttons.Add(leave);
        page.Buttons.Add(TaskDialogButton.Cancel);
        var answer = TaskDialog.ShowDialog(this, page);
        if (answer != stop && answer != leave)
            return null;
        if (page.Verification.Checked)
        {
            host.Settings.OnClose = answer == stop ? "Stop" : "Leave";
            host.Settings.Save();
        }
        return answer == stop;
    }

    /// <summary>Keeper first (it would restart the others), then the server, the serial service last
    /// (catr pcfs down starts it to check what is in front), then any pcfsserver.exe catr doesn't know.</summary>
    private async Task StopBackground()
    {
        statusText.Text = "Stopping catr's background services…";
        Enabled = false;
        if (host.Runner.Running is not null)
        {
            host.Runner.Stop();
            for (var waited = 0; host.Runner.Running is not null && waited < 5000; waited += 100)
                await Task.Delay(100);
        }
        await host.Run(["pcfs", "keep", "--stop"], quiet: true, record: false);
        await host.Run(["pcfs", "down"], quiet: true, yes: true, record: false);
        await host.Run(["serial", "--stop"], quiet: true, record: false);
        foreach (var server in System.Diagnostics.Process.GetProcessesByName("pcfsserver"))
            using (server)
            {
                try { server.Kill(); }
                catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception) { }
            }
    }

    private async void OnClosing(object? sender, FormClosingEventArgs e)
    {
        if (!backgroundStopped && !AppHost.SnapshotMode && BackgroundServices() is { Count: > 0 } services)
        {
            var choice = AskStopBackground(services, host.Runner.Running);
            if (choice is null)
            {
                e.Cancel = true;
                return;
            }
            if (choice == true)
            {
                e.Cancel = true;
                backgroundStopped = true;
                await StopBackground();
                try { host.Settings.Save(); } catch (IOException) { }
                Close();
                return;
            }
            host.Runner.Stop();   // leaving the services, but not a command that writes into this window
        }
        if (host.Runner.Running is { } running)
        {
            if (!Ui.Confirm(this, $"{running} is still running. Close CatR-Tools and stop it?", "Close"))
            {
                e.Cancel = true;
                return;
            }
            host.Runner.Stop();
        }
        try
        {
            host.Settings.Save();
        }
        catch (IOException)
        {
        }
    }

    private Control BuildSidebar()
    {
        var sidebar = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Surface, Padding = new Padding(16, 18, 16, 14) };
        var brand = new Label
        {
            Text = "CatR-Tools", AutoSize = false, Height = 40, Dock = DockStyle.Top, Font = Theme.Brand,
            ForeColor = Theme.Ink, TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(10, 0, 0, 0)
        };
        var brandSub = new Label
        {
            Text = "CAT-R TOOLKIT", AutoSize = false, Height = 31, Dock = DockStyle.Top, Font = Theme.Caption,
            ForeColor = Theme.Muted, TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(11, 0, 0, 0)
        };
        var nav = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false,
            AutoScroll = false, Padding = new Padding(0, 20, 0, 0), BackColor = Theme.Surface
        };
        foreach (var (name, glyph) in new[]
                 {
                     ("Overview", "⌂"), ("Titles", "▦"), ("Install", "⇧"), ("Commands", "⌘"),
                     ("PCFS", "◉"), ("Diagnostics", "⌁"), ("Settings", "⚙")
                 })
        {
            // Labels, not buttons: dark mode draws a border around flat buttons.
            var button = new Label
            {
                Text = $"  {glyph}    {name}", AutoSize = false, Width = 190, Height = 42,
                TextAlign = ContentAlignment.MiddleLeft, Font = Theme.Body, ForeColor = Theme.Muted,
                BackColor = Theme.Surface, Margin = new Padding(0, 0, 0, 5), Cursor = Cursors.Hand
            };
            button.Click += (_, _) => ShowPage(name);
            navButtons[name] = button;
            nav.Controls.Add(button);
        }
        var logButton = new Label
        {
            Text = "  ▣    USB Log", AutoSize = false, Dock = DockStyle.Bottom, Height = 46,
            TextAlign = ContentAlignment.MiddleLeft, Font = Theme.BodyBold, ForeColor = Theme.Accent,
            BackColor = Theme.AccentSoft, Cursor = Cursors.Hand
        };
        logButton.Click += (_, _) => ShowLogWindow();
        sidebar.Controls.Add(nav);
        sidebar.Controls.Add(logButton);
        sidebar.Controls.Add(brandSub);
        sidebar.Controls.Add(brand);
        sidebar.Controls.Add(new Panel { Dock = DockStyle.Right, Width = 1, BackColor = Theme.Line });
        return sidebar;
    }

    private Control BuildMainArea()
    {
        var area = new TableLayoutPanel
        {
            Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2, BackColor = Theme.Canvas,
            Padding = new Padding(20, 0, 20, 14)
        };
        area.RowStyles.Add(new RowStyle(SizeType.Absolute, 82));
        area.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        var top = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Canvas };
        top.Resize += (_, _) => consolePill.Left = Math.Max(8, top.ClientSize.Width - consolePill.Width);
        consolePill.Top = 22;
        top.Controls.AddRange([pageTitle, pageSubtitle, consolePill]);
        area.Controls.Add(top, 0, 0);
        area.Controls.Add(content, 0, 1);
        return area;
    }

    public void ShowPage(string name)
    {
        if (!pages.TryGetValue(name, out var page))
            return;
        foreach (var (key, button) in navButtons)
        {
            var selected = key == name;
            button.BackColor = selected ? Theme.AccentSoft : Theme.Surface;
            button.ForeColor = selected ? Theme.Accent : Theme.Muted;
            button.Font = selected ? Theme.BodyBold : Theme.Body;
        }
        pageTitle.Text = page.Title;
        pageSubtitle.Text = page.Subtitle;
        if (current != page)
        {
            page.Visible = true;
            page.BringToFront();
            if (current is not null)
                current.Visible = false;
            current = page;
        }
        page.Shown();
    }

    public void ShowLogWindow()
    {
        if (logWindow is null || logWindow.IsDisposed)
        {
            logWindow = new LogForm(host);
            logWindow.FormClosed += (_, _) => logWindow = null;
            logWindow.Show(this);
        }
        else
        {
            if (logWindow.WindowState == FormWindowState.Minimized)
                logWindow.WindowState = FormWindowState.Normal;
            logWindow.Activate();
        }
    }

    /// <summary>For build checks: draw every page into PNGs (CATRTOOLS_SNAPSHOT=folder), then exit.</summary>
    public async Task Snapshot(string folder)
    {
        Directory.CreateDirectory(folder);
        if (Environment.GetEnvironmentVariable("CATRTOOLS_TGA") is { Length: > 0 } tga)
        {
            using var icon = Tga.Read(File.ReadAllBytes(tga));
            icon.Save(Path.Combine(folder, "icon.png"));
        }
        foreach (var name in pages.Keys.ToList())
        {
            ShowPage(name);
            await Task.Delay(300);
            for (var waited = 0; host.Runner.Running is not null && waited < 30000; waited += 200)
                await Task.Delay(200);
            await Task.Delay(name == "Overview" ? 2500 : 300);   // host-only status runs after the console check
            using var bitmap = new Bitmap(Width, Height);
            DrawToBitmap(bitmap, new Rectangle(Point.Empty, Size));
            bitmap.Save(Path.Combine(folder, $"{name}.png"));
        }
        ShowLogWindow();
        await Task.Delay(800);
        if (logWindow is not null)
        {
            using var bitmap = new Bitmap(logWindow.Width, logWindow.Height);
            logWindow.DrawToBitmap(bitmap, new Rectangle(Point.Empty, logWindow.Size));
            bitmap.Save(Path.Combine(folder, "UsbLog.png"));
        }
        // Help: a RichTextBox doesn't draw into DrawToBitmap, so this one is copied from the screen.
        foreach (var (topic, file) in new[] { ("Getting started", "Help.png"), ("PCFS", "Help-PCFS.png") })
        {
            HelpForm.Open(this, topic);
            await Task.Delay(800);
            if (Application.OpenForms.OfType<HelpForm>().FirstOrDefault() is { } helpWindow)
            {
                helpWindow.Activate();
                await Task.Delay(300);
                using var bitmap = new Bitmap(helpWindow.Width, helpWindow.Height);
                using (var graphics = Graphics.FromImage(bitmap))
                    graphics.CopyFromScreen(helpWindow.Location, Point.Empty, helpWindow.Size);
                bitmap.Save(Path.Combine(folder, file));
            }
        }
        Close();
    }
}
