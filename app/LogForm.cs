namespace CatRTools;

/// <summary>USB serial output from catr's serial service (the same history `catr log --follow` shows).</summary>
internal sealed class LogForm : Form
{
    private readonly AppHost host;
    private readonly OutputBox output = new() { Dock = DockStyle.Fill };
    private readonly Label state = new() { AutoSize = true, Font = Theme.Small, ForeColor = Theme.Muted, Margin = new Padding(8, 13, 8, 0) };
    private readonly TextBox filter = Ui.TextBox(placeholder: "Show lines containing…");
    private readonly CheckBox pause = Ui.Check("Pause");
    private readonly CheckBox wrap = Ui.Check("Wrap");
    private readonly Button start;
    private readonly CancellationTokenSource stop = new();
    private Font? logFont;
    private string pending = "";

    public LogForm(AppHost host)
    {
        this.host = host;
        Text = "USB Log — CatR-Tools";
        StartPosition = FormStartPosition.CenterParent;
        MinimumSize = new Size(640, 380);
        Size = new Size(980, 600);
        BackColor = Theme.Canvas;
        Font = Theme.Body;
        if (host.Settings.LogBounds is [var x, var y, var w, var h] && Screen.AllScreens.Any(s => s.WorkingArea.IntersectsWith(new Rectangle(x, y, w, h))))
        {
            StartPosition = FormStartPosition.Manual;
            Bounds = new Rectangle(x, y, Math.Max(w, MinimumSize.Width), Math.Max(h, MinimumSize.Height));
        }

        start = Ui.Button("Start serial service", async (_, _) => await StartService(), primary: true);
        start.Visible = false;
        filter.Width = 200;
        filter.Margin = new Padding(0, 9, 8, 4);
        foreach (var check in new[] { pause, wrap })
            check.Margin = new Padding(4, 12, 10, 4);
        wrap.Checked = host.Settings.LogWrap;
        wrap.CheckedChanged += (_, _) =>
        {
            output.WordWrap = wrap.Checked;
            host.Settings.LogWrap = wrap.Checked;
        };
        output.WordWrap = wrap.Checked;
        SetFontSize(host.Settings.LogFontSize);
        var smaller = Ui.Button("A−", (_, _) => SetFontSize(host.Settings.LogFontSize - 1));
        var larger = Ui.Button("A+", (_, _) => SetFontSize(host.Settings.LogFontSize + 1));
        smaller.MinimumSize = larger.MinimumSize = new Size(40, 34);
        // RichTextBox zoom (Ctrl+wheel) resets whenever text is added; change the font instead.
        output.MouseWheel += (_, e) =>
        {
            if (ModifierKeys != Keys.Control) return;
            SetFontSize(host.Settings.LogFontSize + Math.Sign(e.Delta));
            if (e is HandledMouseEventArgs handled) handled.Handled = true;
        };

        var bar = Ui.Row(true,
            Ui.Button("Clear", (_, _) => output.Clear()),
            Ui.Button("Save…", (_, _) => Save()),
            smaller, larger, filter, wrap, pause, start, state);
        bar.Dock = DockStyle.Top;
        bar.Padding = new Padding(12, 4, 12, 4);
        var frame = new Panel { Dock = DockStyle.Fill, Padding = new Padding(12, 8, 6, 8), BackColor = Theme.Terminal };
        frame.Controls.Add(output);
        Controls.Add(frame);
        Controls.Add(bar);
        Theme.Style(this);
        Theme.TitleBar(this);
        Shown += async (_, _) => await Follow(stop.Token);
        FormClosing += (_, _) =>
        {
            var bounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
            host.Settings.LogBounds = [bounds.X, bounds.Y, bounds.Width, bounds.Height];
            try { host.Settings.Save(); } catch (IOException) { }
        };
        FormClosed += (_, _) => { stop.Cancel(); stop.Dispose(); logFont?.Dispose(); };
    }

    private void SetFontSize(int size)
    {
        size = Math.Clamp(size, 7, 24);
        host.Settings.LogFontSize = size;
        var old = logFont;
        logFont = new Font("Consolas", size);
        output.Font = logFont;   // also restyles existing text
        old?.Dispose();
    }

    private async Task Follow(CancellationToken token)
    {
        long? cursor = null;
        while (!token.IsCancellationRequested)
        {
            if (!SerialLog.ServiceFilePresent)
            {
                SetState("Serial service not running. catr starts it with the first serial command (launch, cos, run).", false);
                await Delay(1000, token);
                continue;
            }
            try
            {
                if (pause.Checked)
                {
                    SetState("Paused", true);
                    await Delay(300, token);
                    continue;
                }
                var chunk = await SerialLog.ReadAsync(cursor, token);
                if (chunk.Gap && cursor is not null)
                    output.Line("[serial history gap: older lines were dropped]", Theme.Warn);
                Show(chunk.Text.Replace("\r\n", "\n").Replace('\r', '\n'));
                cursor = chunk.Cursor;
                SetState(chunk.Error is null ? "Following" : $"Serial error: {chunk.Error}", chunk.Error is null);
                await Delay(250, token);
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested)
            {
                return;
            }
            catch (Exception exception) when (exception is IOException or System.Net.Sockets.SocketException or System.Text.Json.JsonException or OperationCanceledException or UnauthorizedAccessException)
            {
                SetState($"Serial service unavailable ({exception.Message}); retrying", false);
                cursor = null;
                await Delay(2000, token);
            }
        }
    }

    private static async Task Delay(int milliseconds, CancellationToken token)
    {
        try { await Task.Delay(milliseconds, token); }
        catch (OperationCanceledException) { }
    }

    private void Show(string text)
    {
        if (text.Length == 0) return;
        var needle = filter.Text;
        if (needle.Length == 0)
        {
            output.Append(text.Replace("\n", Environment.NewLine));
            return;
        }
        var lines = (pending + text).Split('\n');
        pending = lines[^1];
        foreach (var line in lines[..^1])
            if (line.Contains(needle, StringComparison.OrdinalIgnoreCase))
                output.Line(line);
    }

    private void SetState(string text, bool ok)
    {
        if (IsDisposed) return;
        state.Text = text;
        state.ForeColor = ok ? Theme.Muted : Theme.Warn;
        start.Visible = !SerialLog.ServiceFilePresent;
    }

    private async Task StartService()
    {
        start.Enabled = false;
        try
        {
            var result = await host.Run(["serial", "--background"]);
            if (result is { Ok: false })
                Ui.Error(this, result.Message(), "Serial service");
        }
        finally
        {
            start.Enabled = true;
        }
    }

    private void Save()
    {
        using var dialog = new SaveFileDialog { Filter = "Text (*.txt)|*.txt|Log (*.log)|*.log", FileName = $"usb-log-{DateTime.Now:yyyyMMdd-HHmmss}.txt" };
        if (dialog.ShowDialog(this) == DialogResult.OK)
            File.WriteAllText(dialog.FileName, output.Text);
    }
}
