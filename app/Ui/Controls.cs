namespace CatRTools;

/// <summary>Layout helpers. Pages are built once and laid out with docking, TableLayoutPanel and
/// FlowLayoutPanel, so a resize reflows them instead of rebuilding (and losing input).</summary>
internal static class Ui
{
    public static TableLayoutPanel Column(Padding? padding = null)
    {
        var column = new TableLayoutPanel
        {
            ColumnCount = 1,
            Dock = DockStyle.Top,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            GrowStyle = TableLayoutPanelGrowStyle.AddRows,
            Padding = padding ?? Padding.Empty,
            Margin = Padding.Empty
        };
        column.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        return column;
    }

    /// <summary>A page: cards stacked on top, the last control (usually output) fills the rest.</summary>
    public static TableLayoutPanel PageLayout(params Control[] rows)
    {
        var page = new TableLayoutPanel
        {
            Location = Point.Empty,
            ColumnCount = 1,
            RowCount = rows.Length,
            BackColor = Theme.Canvas,
            Margin = Padding.Empty,
            Padding = new Padding(0, 2, 0, 0)
        };
        page.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (var i = 0; i < rows.Length; i++)
        {
            page.RowStyles.Add(i == rows.Length - 1 ? new RowStyle(SizeType.Percent, 100) : new RowStyle(SizeType.AutoSize));
            rows[i].Dock = DockStyle.Fill;
            page.Controls.Add(rows[i], 0, i);
        }
        // Fill the page; when the cards need more height than the window has, grow and let the
        // page scroll so the last row (output) keeps its minimum height.
        var fitting = false;
        void Fit()
        {
            if (fitting || page.Parent is not { } parent) return;
            fitting = true;
            try
            {
                var heights = page.GetRowHeights();
                var cards = 0;
                for (var i = 0; i < rows.Length - 1 && i < heights.Length; i++)
                    cards += heights[i];
                var last = rows[^1];
                var needed = cards + Math.Max(160, last.MinimumSize.Height) + last.Margin.Vertical + page.Padding.Vertical;
                page.Size = new Size(parent.ClientSize.Width, Math.Max(parent.ClientSize.Height, needed));
            }
            finally
            {
                fitting = false;
            }
        }
        page.ParentChanged += (_, _) =>
        {
            if (page.Parent is ScrollableControl scrollable)
            {
                scrollable.AutoScroll = true;
                scrollable.Resize += (_, _) => Fit();
                Fit();
            }
        };
        page.Layout += (_, _) => page.BeginInvokeIfReady(Fit);
        return page;
    }

    /// <summary>A white bordered card; put content in the returned body column.</summary>
    public static Panel Card(string? title, string? description, out TableLayoutPanel body)
    {
        var card = new Panel
        {
            BackColor = Theme.Surface,
            BorderStyle = BorderStyle.FixedSingle,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Margin = new Padding(0, 0, 0, 12),
            Padding = new Padding(16, 12, 16, 12)
        };
        body = Column();
        if (title is not null)
            body.Controls.Add(new Label { Text = title, AutoSize = true, Font = Theme.CardTitle, ForeColor = Theme.Ink, Margin = new Padding(0, 0, 0, 4) });
        if (description is not null)
            body.Controls.Add(Note(description));
        card.Controls.Add(body);
        return card;
    }

    public static Label Note(string text, Color? color = null) => new()
    {
        Text = text,
        AutoSize = true,
        Dock = DockStyle.Fill,
        Font = Theme.Small,
        ForeColor = color ?? Theme.Muted,
        Margin = new Padding(0, 0, 0, 6)
    };

    public static Label Caption(string text) => new()
    {
        Text = text,
        AutoSize = true,
        Font = Theme.Caption,
        ForeColor = Theme.Muted,
        Margin = new Padding(0, 6, 0, 3)
    };

    /// <summary>A row of controls. It doesn't wrap: a wrapping FlowLayoutPanel in an AutoSize
    /// TableLayoutPanel reports the height it would need at a narrow width (blank space below).</summary>
    public static FlowLayoutPanel Row(params Control[] controls) => Row(false, controls);

    public static FlowLayoutPanel Row(bool wrap, params Control[] controls)
    {
        var row = new FlowLayoutPanel
        {
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = wrap,
            Margin = new Padding(0, 2, 0, 2),
            BackColor = Color.Transparent
        };
        row.Controls.AddRange(controls);
        return row;
    }

    public static Button Button(string text, EventHandler? click = null, bool primary = false)
    {
        var button = new Button
        {
            Text = text,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            MinimumSize = new Size(96, 34),
            Padding = new Padding(8, 0, 8, 0),
            FlatStyle = FlatStyle.Flat,
            BackColor = primary ? Theme.Accent : Theme.Surface,
            ForeColor = primary ? Color.White : Theme.Slate,
            Font = primary ? Theme.BodyBold : Theme.Small,
            Margin = new Padding(0, 4, 8, 4),
            UseVisualStyleBackColor = false
        };
        button.FlatAppearance.BorderColor = primary ? Theme.Accent : Theme.Line;
        if (click is not null)
            button.Click += click;
        return button;
    }

    public static LinkLabel Link(string text, Action click)
    {
        var link = new LinkLabel
        {
            Text = text,
            AutoSize = true,
            Font = Theme.Small,
            LinkColor = Theme.Accent,
            ActiveLinkColor = Theme.Accent,
            LinkBehavior = LinkBehavior.HoverUnderline,
            Margin = new Padding(0, 2, 16, 6)
        };
        link.LinkClicked += (_, _) => click();
        return link;
    }

    public static CheckBox Check(string text, bool value = false) => new()
    {
        Text = text,
        Checked = value,
        AutoSize = true,
        Font = Theme.Small,
        ForeColor = Theme.Ink,
        Margin = new Padding(0, 4, 18, 4)
    };

    /// <summary>A path box with buttons beside it; the box stretches with the card.</summary>
    public static TableLayoutPanel PathRow(TextBox box, params Button[] buttons)
    {
        var row = new TableLayoutPanel
        {
            ColumnCount = 1 + buttons.Length,
            RowCount = 1,
            Dock = DockStyle.Fill,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Margin = new Padding(0, 0, 0, 4)
        };
        row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, 9, 8, 4);
        row.Controls.Add(box, 0, 0);
        for (var i = 0; i < buttons.Length; i++)
        {
            row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            row.Controls.Add(buttons[i], i + 1, 0);
        }
        return row;
    }

    public static TextBox TextBox(string? text = null, string? placeholder = null, bool mono = false) => new()
    {
        Text = text ?? "",
        PlaceholderText = placeholder ?? "",
        Font = mono ? Theme.Mono : Theme.Body,
        BackColor = Theme.Field,
        BorderStyle = BorderStyle.FixedSingle
    };

    public static FlowLayoutPanel CardRow() => new()
    {
        AutoSize = true,
        AutoSizeMode = AutoSizeMode.GrowAndShrink,
        FlowDirection = FlowDirection.LeftToRight,
        WrapContents = true,
        Margin = new Padding(0, 0, 0, 6),
        BackColor = Theme.Canvas
    };

    public static void Error(IWin32Window owner, string message, string title = "CatR-Tools") =>
        MessageBox.Show(owner, message, title, MessageBoxButtons.OK, MessageBoxIcon.Warning);

    public static bool Confirm(IWin32Window owner, string message, string title = "Confirm") =>
        MessageBox.Show(owner, message, title, MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
            MessageBoxDefaultButton.Button2) == DialogResult.Yes;

    public static string? PickFolder(IWin32Window owner, string description, string? start)
    {
        using var dialog = new FolderBrowserDialog
        {
            Description = description,
            UseDescriptionForTitle = true,
            ShowNewFolderButton = true
        };
        if (!string.IsNullOrWhiteSpace(start) && Directory.Exists(start))
            dialog.InitialDirectory = start;
        return dialog.ShowDialog(owner) == DialogResult.OK ? dialog.SelectedPath : null;
    }

    public static void OpenFolder(IWin32Window owner, string? path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
        {
            Error(owner, $"Folder not found:\n{path}");
            return;
        }
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
    }
}

internal static class ControlExtensions
{
    /// <summary>Runs after the current layout pass (avoids resizing inside Layout).</summary>
    public static void BeginInvokeIfReady(this Control control, Action action)
    {
        if (control.IsHandleCreated && !control.IsDisposed)
            control.BeginInvoke(action);
    }
}

/// <summary>A status tile: heading, value, detail. Fixed size; a FlowLayoutPanel reflows them.</summary>
internal sealed class MetricCard : Panel
{
    private readonly Label value = new() { AutoSize = false, Font = Theme.Value, ForeColor = Theme.Ink, AutoEllipsis = true };
    private readonly Label detail = new() { AutoSize = false, Font = Theme.Small, ForeColor = Theme.Muted, AutoEllipsis = true };
    private readonly Panel stripe = new() { Dock = DockStyle.Left, Width = 4, BackColor = Theme.Line };

    public MetricCard(string heading, string initial = "—", string note = "", int width = 236)
    {
        Size = new Size(width, 104);
        BackColor = Theme.Surface;
        BorderStyle = BorderStyle.FixedSingle;
        Margin = new Padding(0, 0, 12, 12);
        var label = new Label { Text = heading, AutoSize = true, Font = Theme.Caption, ForeColor = Theme.Muted, Location = new Point(16, 12) };
        value.SetBounds(16, 36, width - 28, 26);
        detail.SetBounds(16, 66, width - 28, 34);
        Controls.AddRange([value, detail, label, stripe]);
        Set(initial, note);
    }

    public void Set(string text, string note = "", Color? state = null)
    {
        value.Text = text;
        detail.Text = note;
        stripe.BackColor = state ?? Theme.Line;
    }
}

/// <summary>Dark output pane for catr's stdout/stderr. Keeps the last ~1.5 M characters.</summary>
internal sealed class OutputBox : RichTextBox
{
    private const int Limit = 1_500_000;

    public OutputBox()
    {
        ReadOnly = true;
        BorderStyle = BorderStyle.None;
        BackColor = Theme.Terminal;
        ForeColor = Theme.TerminalText;
        Font = Theme.Mono;
        DetectUrls = false;
        WordWrap = false;
        HideSelection = false;
        Margin = new Padding(0, 0, 0, 4);
        MinimumSize = new Size(0, 120);
    }

    public void Append(string text, Color? color = null)
    {
        if (IsDisposed)
            return;
        if (TextLength > Limit)
        {
            Select(0, TextLength - Limit / 2);
            SelectedText = "";
        }
        var follow = SelectionStart >= TextLength - 1 || SelectionLength == 0;
        SelectionStart = TextLength;
        SelectionLength = 0;
        SelectionColor = color ?? Theme.TerminalText;
        AppendText(text);
        if (follow)
            ScrollToCaret();
    }

    public void Line(string text, Color? color = null) => Append(text + Environment.NewLine, color);
}

/// <summary>An output pane with a small header (label, Clear) for the bottom of a page.</summary>
internal sealed class OutputPanel : Panel
{
    public OutputBox Box { get; } = new() { Dock = DockStyle.Fill };

    public OutputPanel(string caption = "OUTPUT")
    {
        BackColor = Theme.Terminal;
        Padding = new Padding(12, 6, 6, 8);
        MinimumSize = new Size(0, 160);
        var header = new Panel { Dock = DockStyle.Top, Height = 26, BackColor = Theme.Terminal };
        header.Controls.Add(new Label { Text = caption, AutoSize = true, Font = Theme.Caption, ForeColor = Theme.TerminalDim, Location = new Point(0, 6) });
        var clear = new LinkLabel { Text = "Clear", AutoSize = true, Font = Theme.Small, LinkColor = Theme.TerminalDim, ActiveLinkColor = Theme.TerminalText, Anchor = AnchorStyles.Top | AnchorStyles.Right };
        clear.LinkClicked += (_, _) => Box.Clear();
        header.Controls.Add(clear);
        header.Resize += (_, _) => clear.Location = new Point(header.Width - clear.Width - 4, 5);
        Controls.Add(Box);
        Controls.Add(header);
    }
}
