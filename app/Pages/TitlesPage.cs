namespace CatRTools;

internal sealed class TitlesPage : Page
{
    private readonly DataGridView grid = new()
    {
        ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false,
        RowHeadersVisible = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
        SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
        BackgroundColor = Theme.Surface, BorderStyle = BorderStyle.None, GridColor = Theme.Line,
        EnableHeadersVisualStyles = false, CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal,
        ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing, ColumnHeadersHeight = 32
    };
    private readonly ComboBox kind = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 140, Font = Theme.Small, Margin = new Padding(0, 9, 8, 4) };
    private readonly TextBox search = Ui.TextBox(placeholder: "Search name or ID");
    private readonly Label count = new() { AutoSize = true, Font = Theme.Small, ForeColor = Theme.Muted, Margin = new Padding(4, 13, 0, 0) };
    private readonly Button refresh, launch, uninstall, details;
    private readonly OutputPanel output = new();

    public TitlesPage(AppHost host) : base(host)
    {
        Name = "Titles";
        refresh = Ui.Button("Refresh from console", async (_, _) => await Reload(), primary: true);
        launch = Ui.Button("Launch", async (_, _) => await Launch(Selected));
        uninstall = Ui.Button("Uninstall…", async (_, _) => await Uninstall(Selected));
        details = Ui.Button("Details", (_, _) => OpenDetails(Selected));
        kind.Items.AddRange(["All types", "Game", "Update", "DLC", "Demo", "System app", "System data", "Applet", "Other"]);
        kind.SelectedIndex = 0;
        kind.SelectedIndexChanged += (_, _) => Fill();
        search.Width = 220;
        search.Margin = new Padding(0, 9, 8, 4);
        search.TextChanged += (_, _) => Fill();

        var toolbar = Ui.Card("Installed titles", null, out var body);
        body.Controls.Add(Ui.Row(refresh, kind, search, count));
        body.Controls.Add(Ui.Row(launch, uninstall, details));

        grid.ColumnHeadersDefaultCellStyle = new DataGridViewCellStyle { BackColor = Theme.Field, ForeColor = Theme.Muted, Font = Theme.Caption, SelectionBackColor = Theme.Field };
        grid.DefaultCellStyle = new DataGridViewCellStyle { Font = Theme.Small, BackColor = Theme.Surface, ForeColor = Theme.Ink, SelectionBackColor = Theme.AccentSoft, SelectionForeColor = Theme.Ink, Padding = new Padding(4, 0, 4, 0) };
        grid.RowTemplate.Height = 40;
        grid.Columns.Add(new DataGridViewImageColumn
        {
            Name = "icon", HeaderText = "", Width = 46, AutoSizeMode = DataGridViewAutoSizeColumnMode.None,
            ImageLayout = DataGridViewImageCellLayout.Zoom, DefaultCellStyle = { NullValue = null, Padding = new Padding(4, 3, 4, 3) }
        });
        grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "name", HeaderText = "NAME", FillWeight = 44 });
        grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "id", HeaderText = "TITLE ID", FillWeight = 20, DefaultCellStyle = { Font = Theme.MonoSmall } });
        grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "kind", HeaderText = "TYPE", FillWeight = 12 });
        grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "version", HeaderText = "VERSION", FillWeight = 9 });
        grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "location", HeaderText = "DEVICE", FillWeight = 9 });
        grid.CellDoubleClick += (_, e) => { if (e.RowIndex >= 0) OpenDetails(Selected); };
        grid.SelectionChanged += (_, _) => UpdateButtons();
        grid.KeyDown += (_, e) =>
        {
            if (e.KeyCode != Keys.Enter) return;
            e.Handled = true;
            OpenDetails(Selected);
        };
        var menu = new ContextMenuStrip();
        menu.Items.Add("Details", null, (_, _) => OpenDetails(Selected));
        menu.Items.Add("Launch", null, async (_, _) => await Launch(Selected));
        menu.Items.Add("Uninstall…", null, async (_, _) => await Uninstall(Selected));
        menu.Items.Add("Copy title ID", null, (_, _) => { if (Selected is { } t) Clipboard.SetText(t.Id); });
        grid.ContextMenuStrip = menu;
        grid.CellMouseDown += (_, e) =>
        {
            if (e.Button == MouseButtons.Right && e.RowIndex >= 0)
                grid.CurrentCell = grid.Rows[e.RowIndex].Cells[1];
        };

        var gridCard = new Panel { BackColor = Theme.Surface, BorderStyle = BorderStyle.FixedSingle, Margin = new Padding(0, 0, 0, 12), MinimumSize = new Size(0, 200) };
        grid.Dock = DockStyle.Fill;
        gridCard.Controls.Add(grid);
        output.MinimumSize = new Size(0, 130);

        var split = new SplitContainer { Orientation = Orientation.Horizontal, BackColor = Theme.Canvas, SplitterWidth = 6, FixedPanel = FixedPanel.Panel2 };
        split.Panel1.Controls.Add(gridCard);
        gridCard.Dock = DockStyle.Fill;
        split.Panel2.Controls.Add(output);
        output.Dock = DockStyle.Fill;
        // Place the splitter once the split has a real height (at creation it is still tiny,
        // and SplitterDistance throws outside Panel1MinSize..Height-Panel2MinSize).
        var placed = false;
        split.SizeChanged += (_, _) =>
        {
            if (placed || split.Height < 300) return;
            placed = true;
            var wanted = split.Height - 170;
            split.SplitterDistance = Math.Clamp(wanted, split.Panel1MinSize, split.Height - split.Panel2MinSize - split.SplitterWidth);
        };
        Controls.Add(Ui.PageLayout(toolbar, split));
        host.TitlesChanged += () => { ScanPcfs(); Fill(); };
        ScanPcfs();
        Fill();
    }

    public override string Title => "Titles";
    public override string Subtitle => "Installed titles from MCP's title list, and titles staged in the PCFS folder on this PC.";

    /// <summary>Titles staged in the PCFS folder, read from this PC (no console access).</summary>
    private List<TitleInfo> pcfsTitles = [];

    private string? PcfsFolder => CatrLauncher.PcfsRoot(Host.Settings);

    private void ScanPcfs() => pcfsTitles = TitleInfo.ScanPcfs(PcfsFolder);

    public override async void Shown()
    {
        ScanPcfs();
        Fill();
        if (Host.TitlesStale && Host.Runner.Running is null)
        {
            Host.TitlesStale = false;
            await Reload();
        }
        else if (!iconsTried && Host.Titles.Items.Count > 0 && !AppHost.SnapshotMode)
        {
            await FetchIcons();
        }
    }

    private readonly Dictionary<string, DataGridViewRow> rowsById = [];
    private readonly HashSet<string> iconFailed = [];
    private bool iconsTried, fetchingIcons;
    private string iconProgress = "";

    /// <summary>Icons for titles that have none cached, a few titles per catr run. Between runs it
    /// gives way to any other command (stopping catr mid-read would leak IOSU handles).</summary>
    private async Task FetchIcons()
    {
        if (fetchingIcons) return;
        iconsTried = true;
        var missing = Host.Titles.Items.Where(t => t.Path is not null && !Icons.Cached(t) && !iconFailed.Contains(t.Id)).ToList();
        if (missing.Count == 0) return;
        fetchingIcons = true;
        var done = 0;
        try
        {
            foreach (var batch in missing.Chunk(8))
            {
                while (Host.Runner.Running is not null || Host.Waiting > 0)
                    await Task.Delay(300);
                iconProgress = $" · icons {done}/{missing.Count}";
                UpdateCount();
                var result = await Host.Run(["icons", .. batch.Select(t => t.Id)], quiet: true, showJson: false,
                    onStdoutLine: line => ShowIcon(line, ref done, missing.Count), record: false);
                if (result is null || (!result.Ok && !result.Stopped))
                    break;   // console unreachable: try again on the next refresh
            }
        }
        finally
        {
            fetchingIcons = false;
            iconProgress = "";
            UpdateCount();
        }
    }

    private void ShowIcon(string line, ref int done, int total)
    {
        try
        {
            using var item = System.Text.Json.JsonDocument.Parse(line);
            var id = item.RootElement.GetProperty("title").GetString() ?? "";
            done++;
            if (item.RootElement.TryGetProperty("error", out _))
                iconFailed.Add(id);
            else if (rowsById.TryGetValue(id, out var row) && row.Tag is TitleInfo title && row.DataGridView is not null)
                row.Cells[0].Value = Icons.Get(title);
            iconProgress = $" · icons {done}/{total}";
            UpdateCount();
        }
        catch (Exception exception) when (exception is System.Text.Json.JsonException or KeyNotFoundException or InvalidOperationException)
        {
        }
    }

    private TitleInfo? Selected => grid.CurrentRow?.Tag as TitleInfo;

    private void UpdateButtons()
    {
        var title = Selected;
        launch.Enabled = details.Enabled = title is not null;
        uninstall.Enabled = title is not null && (title.Removable || title.IsPcfs);
        uninstall.Text = title?.IsPcfs == true ? "Remove from PCFS…" : "Uninstall…";
    }

    private void Fill()
    {
        var filter = kind.SelectedIndex <= 0 ? null : kind.SelectedItem as string;
        var text = search.Text.Trim();
        var selected = Selected?.Id;
        grid.SuspendLayout();
        grid.Rows.Clear();
        rowsById.Clear();
        var shown = 0;
        hiddenCount = 0;
        foreach (var title in pcfsTitles.Concat(Host.Titles.Items))
        {
            if (!Listed(title)) { hiddenCount++; continue; }
            if (filter is not null && title.Kind != filter) continue;
            if (text.Length > 0 && !title.Id.Contains(text, StringComparison.OrdinalIgnoreCase)
                                && title.Name?.Contains(text, StringComparison.OrdinalIgnoreCase) != true) continue;
            var row = grid.Rows[grid.Rows.Add((object?)Icons.Get(title) ?? DBNull.Value, title.DisplayName, title.Id, title.Kind, title.Version?.ToString() ?? "", title.IsPcfs ? "PCFS" : title.Location)];
            row.Tag = title;
            rowsById[title.Id] = row;
            if (title.Name is null)
                row.Cells[1].Style.ForeColor = Theme.Muted;
            if (title.Id == selected)
                grid.CurrentCell = row.Cells[1];
            shown++;
        }
        grid.ResumeLayout();
        shownCount = shown;
        UpdateCount();
        UpdateButtons();
    }

    private int shownCount, hiddenCount;

    /// <summary>SLC, DRH and BT titles never; system titles only with the setting (menus always);
    /// unnamed titles unless the setting hides them.</summary>
    private bool Listed(TitleInfo title)
    {
        if (title.HiddenDevice) return false;
        if (title.IsSystem && !title.IsMenu && !Host.Settings.ShowSystemTitles) return false;
        if (title.Name is null && Host.Settings.HideUnnamedTitles) return false;
        return true;
    }

    private void UpdateCount()
    {
        var read = Host.Titles.Read is { } time ? $"read {time:g}" : "not read yet";
        var total = Host.Titles.Items.Count + pcfsTitles.Count;
        var hidden = hiddenCount > 0 ? $" ({hiddenCount} hidden, see Settings)" : "";
        count.Text = total == 0 ? $"No titles ({read})" : $"{shownCount} shown{hidden} · {read}{iconProgress}";
    }

    private async Task Reload()
    {
        refresh.Enabled = false;
        try
        {
            var result = await Host.Run(["titles", "--json"], output.Box, showJson: false);
            if (result?.Ok == true && result.Json() is { } list)
            {
                Host.Titles = new TitleCache { Read = DateTime.Now, Items = list.EnumerateArray().Select(TitleInfo.From).ToList() };
                Host.Titles.Save();
                Host.NotifyTitles();
                output.Box.Line($"{Host.Titles.Items.Count} titles", Theme.TerminalDim);
                _ = FetchIcons();
            }
        }
        finally
        {
            refresh.Enabled = true;
        }
    }

    private void OpenDetails(TitleInfo? title)
    {
        if (title is null) return;
        using var form = new TitleDetailForm(Host, title);
        form.ShowDialog(this);
        if (form.Choice == TitleAction.Launch)
            _ = Launch(title);
        else if (form.Choice == TitleAction.Uninstall)
            _ = Uninstall(title);
    }

    private async Task Launch(TitleInfo? title)
    {
        if (title is null) return;
        if (title.LaunchWarning is { } warning &&
            !Ui.Confirm(this, $"Launch {title.DisplayName} ({title.Id})?\n\n{warning}\n\nIf it happens, check the default title " +
                              "(catr default-title) and go back to the Recovery Menu.", "Launch anyway?"))
            return;
        // No dialog for other titles: the click is the confirmation. catr sends the launch once and watches it.
        output.Box.Line($"Launching {title.DisplayName} ({title.Id})", Theme.TerminalDim);
        if (!title.IsPcfs)
        {
            await Host.Run(["launch", title.Id], output.Box, yes: true);
            return;
        }
        // A staged copy: catr run mounts PCFS first if it isn't (that restarts the console).
        var args = new List<string> { "run", title.Id, "--no-follow" };
        var result = await Host.Run(args, output.Box, yes: true);
        if (result?.Ok == true && Host.Settings.OpenLogAfterRun)
            Host.ShowLog();
    }

    private async Task Uninstall(TitleInfo? title)
    {
        if (title?.IsPcfs == true)
        {
            RemovePcfs(title);
            return;
        }
        if (title is null || !title.Removable) return;
        var result = await Host.Run(["uninstall", title.Id], output.Box,
            confirm: $"Uninstall {title.DisplayName} ({title.Id}) from the console?\n\nThis deletes the installed {title.Kind.ToLowerInvariant()}; it can't be undone here (reinstall from an image).");
        if (result?.Ok == true)
            await Reload();
    }

    /// <summary>Removes a staged title's folder from the PCFS folder on this PC (Recycle Bin by default).
    /// Nothing on the console changes; the title's save folder (usr\save) is left alone.</summary>
    private void RemovePcfs(TitleInfo title)
    {
        var folder = title.HostFolder!;
        var mode = Host.Settings.PcfsRemoval;
        var question = $"{title.DisplayName} ({title.Id})\n{folder}\n\nIts save data (usr\\save) is kept.";
        if (mode == "Ask")
        {
            var trash = new TaskDialogButton("Recycle Bin");
            var delete = new TaskDialogButton("Delete permanently");
            var page = new TaskDialogPage
            {
                Caption = "Remove from PCFS", Heading = "Remove this title from the PCFS folder?", Text = question,
                DefaultButton = trash
            };
            page.Buttons.Add(trash);
            page.Buttons.Add(delete);
            page.Buttons.Add(TaskDialogButton.Cancel);
            var answer = TaskDialog.ShowDialog(this, page);
            if (answer == trash) mode = "Trash";
            else if (answer == delete) mode = "Delete";
            else return;
        }
        else if (!Ui.Confirm(this, (mode == "Delete"
                     ? "Delete this title from the PCFS folder permanently?\n\n"
                     : "Move this title from the PCFS folder to the Recycle Bin?\n\n") + question))
        {
            return;
        }
        try
        {
            if (mode == "Delete")
                Directory.Delete(folder, recursive: true);
            else
                Microsoft.VisualBasic.FileIO.FileSystem.DeleteDirectory(folder,
                    Microsoft.VisualBasic.FileIO.UIOption.OnlyErrorDialogs, Microsoft.VisualBasic.FileIO.RecycleOption.SendToRecycleBin);
            output.Box.Line($"{(mode == "Delete" ? "Deleted" : "Moved to the Recycle Bin")}: {folder}", Theme.TerminalDim);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or OperationCanceledException)
        {
            output.Box.Line($"Could not remove {folder}: {exception.Message}", Theme.TerminalError);
            Ui.Error(this, $"Could not remove the folder (is the title running from PCFS?)\n\n{exception.Message}");
        }
        ScanPcfs();
        Fill();
    }
}

internal enum TitleAction { None, Launch, Uninstall }

internal sealed class TitleDetailForm : Form
{
    public TitleAction Choice { get; private set; }

    public TitleDetailForm(AppHost host, TitleInfo title)
    {
        Text = $"{title.DisplayName} — Title";
        StartPosition = FormStartPosition.CenterParent;
        MinimumSize = new Size(560, 330);
        Size = new Size(640, 360);
        BackColor = Theme.Canvas;
        Font = Theme.Body;
        var card = Ui.Card(title.DisplayName, null, out var body);
        card.Dock = DockStyle.Fill;
        card.AutoSize = false;
        var facts = new Label
        {
            Text = $"Title ID   {title.Id}\nType        {title.Kind}\nVersion    {title.Version?.ToString() ?? "—"}\nDevice      {(title.IsPcfs ? "PCFS (this PC)" : title.Location)}\nPath         {title.HostFolder ?? title.Path ?? "—"}",
            AutoSize = true, Font = Theme.Small, ForeColor = Theme.Slate, Margin = new Padding(0, 4, 0, 10)
        };
        if (Icons.Get(title, 96) is { } icon)
            body.Controls.Add(Ui.Row(new PictureBox { Image = icon, Size = new Size(96, 96), SizeMode = PictureBoxSizeMode.Zoom, Margin = new Padding(0, 4, 16, 10) }, facts));
        else
            body.Controls.Add(facts);
        MinimumSize = new Size(560, 400);
        Size = new Size(660, 420);
        var launch = Ui.Button("Launch", (_, _) => { Choice = TitleAction.Launch; Close(); }, primary: true);
        var uninstall = Ui.Button(title.IsPcfs ? "Remove from PCFS…" : "Uninstall from console…", (_, _) => { Choice = TitleAction.Uninstall; Close(); });
        uninstall.Visible = title.Removable || title.IsPcfs;
        var meta = Ui.Button("meta.xml", (_, _) =>
        {
            using var xml = new XmlInfoForm(host, title);
            xml.ShowDialog(this);
        });
        meta.Enabled = title.Path is not null || title.IsPcfs;
        var copy = Ui.Button("Copy ID", (_, _) => Clipboard.SetText(title.Id));
        body.Controls.Add(Ui.Row(launch, uninstall, meta, copy));
        body.Controls.Add(Ui.Note(title.IsPcfs
            ? "Staged in the PCFS folder on this PC. Launch mounts PCFS first if it isn't (that restarts the console). Remove deletes the folder here (Recycle Bin by default, see Settings)."
            : title.Removable
                ? "Uninstall removes the installed title from MLC. catr refuses SCT, the Recovery Menu and system titles."
                : "The system doesn't allow uninstalling menu apps or system essentials. Only games, updates and DLC on MLC or USB can be uninstalled."));
        var frame = new Panel { Dock = DockStyle.Fill, Padding = new Padding(16), BackColor = Theme.Canvas };
        frame.Controls.Add(card);
        Controls.Add(frame);
        Theme.Style(this);
        Theme.TitleBar(this);
    }
}

/// <summary>meta.xml, copied off the console with catr get (reads only).</summary>
internal sealed class XmlInfoForm : Form
{
    private readonly OutputBox xml = new() { Dock = DockStyle.Fill, WordWrap = true };

    public XmlInfoForm(AppHost host, TitleInfo title)
    {
        Text = $"{title.DisplayName} — meta.xml";
        StartPosition = FormStartPosition.CenterParent;
        MinimumSize = new Size(560, 400);
        Size = new Size(780, 600);
        BackColor = Theme.Terminal;
        Padding = new Padding(12);
        Font = Theme.Body;
        Controls.Add(xml);
        Theme.Style(this);
        Theme.TitleBar(this);
        Load += async (_, _) =>
        {
            if (title.HostFolder is { } folder)
            {
                var file = Path.Combine(folder, "meta", "meta.xml");
                xml.Line(file, Theme.TerminalDim);
                xml.Line("");
                try { xml.Append(File.ReadAllText(file)); }
                catch (IOException exception) { xml.Line(exception.Message, Theme.TerminalError); }
                xml.SelectionStart = 0;
                return;
            }
            var local = Path.Combine(Path.GetTempPath(), "CatR-Tools", $"meta-{title.Id}.xml");
            Directory.CreateDirectory(Path.GetDirectoryName(local)!);
            var source = $"{title.Path}/meta/meta.xml";
            var result = await host.Run(["get", source, local, "--force"], xml);
            if (result?.Ok == true && File.Exists(local))
            {
                xml.Clear();
                xml.Line(source, Theme.TerminalDim);
                xml.Line("");
                xml.Append(File.ReadAllText(local));
                xml.SelectionStart = 0;
                xml.ScrollToCaret();
            }
        };
    }
}
