namespace CatRTools;

internal sealed class CommandsPage : Page
{
    private readonly TextBox input = Ui.TextBox(mono: true);
    private readonly Button catrMode, cosMode, run;
    private readonly Label hint = Ui.Note("");
    private readonly FlowLayoutPanel picks = Ui.Row(wrap: true);
    private readonly OutputPanel output = new();
    private bool cos;
    private int historyIndex = -1;

    private static readonly string[] CatrPicks = ["info", "titles", "default-title", "pcfs status", "serial --status", "debug threads", "syslog --tail 80", "ls /vol/storage_mlc01/usr/tmp"];
    private static readonly string[] CosPicks = ["sdkversion", "help", "threads", "modules", "heaps", "cpuusage"];

    public CommandsPage(AppHost host) : base(host)
    {
        Name = "Commands";
        catrMode = ModeButton("catr command", false);
        cosMode = ModeButton("COS shell", true);
        run = Ui.Button("Run", async (_, _) => await RunInput(), primary: true);
        input.KeyDown += async (_, e) =>
        {
            if (e.KeyCode == Keys.Enter)
            {
                e.SuppressKeyPress = true;
                await RunInput();
            }
            else if (e.KeyCode is Keys.Up or Keys.Down)
            {
                e.SuppressKeyPress = true;
                Browse(e.KeyCode == Keys.Up ? -1 : 1);
            }
        };
        var card = Ui.Card(null, null, out var body);
        body.Controls.Add(Ui.Row(catrMode, cosMode));
        body.Controls.Add(hint);
        body.Controls.Add(Ui.PathRow(input, run));
        body.Controls.Add(picks);
        Controls.Add(Ui.PageLayout(card, output));
        SetMode(false);
    }

    public override string Title => "Commands";
    public override string Subtitle => "Run any catr command, or send one COS shell command over USB serial.";

    public override void Shown() => input.Focus();

    private List<string> History => cos ? Host.Settings.CosHistory : Host.Settings.CommandHistory;

    private Button ModeButton(string text, bool isCos)
    {
        var button = Ui.Button(text, (_, _) => SetMode(isCos));
        button.MinimumSize = new Size(140, 34);
        return button;
    }

    private void SetMode(bool isCos)
    {
        cos = isCos;
        historyIndex = -1;
        foreach (var (button, selected) in new[] { (catrMode, !cos), (cosMode, cos) })
        {
            button.BackColor = selected ? Theme.Accent : Theme.Surface;
            button.ForeColor = selected ? Color.White : Theme.Slate;
            button.Font = selected ? Theme.BodyBold : Theme.Small;
        }
        hint.Text = cos
            ? "Sent as catr cos: any single command, sent once; only read-only commands are ever resent. The reply comes from the shared serial log."
            : "Arguments after \"catr\". Commands that change the console ask here first (catr needs --yes without a terminal). ↑/↓ browse history.";
        input.PlaceholderText = cos ? "e.g. sdkversion" : "e.g. titles --type 00050000";
        picks.SuspendLayout();
        picks.Controls.Clear();
        foreach (var pick in cos ? CosPicks : CatrPicks)
        {
            var button = Ui.Button(pick, (_, _) =>
            {
                input.Text = pick;
                input.SelectionStart = input.TextLength;
                input.Focus();
            });
            button.Font = Theme.MonoSmall;
            button.MinimumSize = new Size(0, 30);
            picks.Controls.Add(button);
        }
        picks.ResumeLayout();
        input.Focus();
    }

    private void Browse(int step)
    {
        var history = History;
        if (history.Count == 0) return;
        historyIndex = historyIndex < 0 ? (step < 0 ? history.Count - 1 : -1) : historyIndex + step;
        if (historyIndex < 0 || historyIndex >= history.Count)
        {
            historyIndex = -1;
            input.Clear();
            return;
        }
        input.Text = history[historyIndex];
        input.SelectionStart = input.TextLength;
    }

    private async Task RunInput()
    {
        var line = input.Text.Trim();
        if (line.Length == 0 || !run.Enabled) return;
        List<string> args;
        if (cos)
        {
            // "cos kill" typed in COS mode is sent as cos kill, not cos cos kill.
            var words = line.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToList();
            if (words.Count > 0 && words[0].Equals("catr", StringComparison.OrdinalIgnoreCase))
                words.RemoveAt(0);
            if (words.Count > 0 && words[0].Equals("cos", StringComparison.OrdinalIgnoreCase))
                words.RemoveAt(0);
            if (words.Count == 0) return;
            line = string.Join(' ', words);
            args = ["cos", .. words];
        }
        else
        {
            args = CatrLauncher.Split(line);
            if (args.Count > 0 && args[0].Equals("catr", StringComparison.OrdinalIgnoreCase))
                args.RemoveAt(0);
            if (args.Count == 0) return;
        }
        AppSettings.Remember(History, line);
        Host.Settings.Save();
        historyIndex = -1;
        input.Clear();
        run.Enabled = false;
        try
        {
            await Host.Run(args, output.Box);
        }
        finally
        {
            run.Enabled = true;
            input.Focus();
        }
    }
}
