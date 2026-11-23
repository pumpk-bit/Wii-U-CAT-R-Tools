using System.Reflection;
using System.Text.RegularExpressions;

namespace CatRTools;

/// <summary>Help > Documentation (F1): the README that ships next to CatR-Tools.exe, built into it
/// (help.md: the app's guide and catr's command reference), with a topic list and search.</summary>
internal sealed partial class HelpForm : Form
{
    private static HelpForm? open;

    private readonly ListBox topics = new()
    {
        Dock = DockStyle.Fill, BorderStyle = BorderStyle.None, IntegralHeight = false, Font = Theme.Body,
        DrawMode = DrawMode.OwnerDrawFixed, ItemHeight = 26
    };
    private readonly RichTextBox text = new()
    {
        Dock = DockStyle.Fill, ReadOnly = true, BorderStyle = BorderStyle.None, DetectUrls = true,
        WordWrap = true, ScrollBars = RichTextBoxScrollBars.Vertical
    };
    private readonly TextBox search = Ui.TextBox(placeholder: "Search (Enter: next match)");
    private readonly List<(string Title, int Level, int Start)> sections = [];
    private int searchFrom;

    /// <summary>The documentation, from the help.md resource (a build without it: the app's README).</summary>
    public static string Markdown()
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("help.md");
        if (stream is not null)
            return new StreamReader(stream).ReadToEnd();
        return "# Documentation\n\nThis build has no documentation built in. Build CatR-Tools.exe with build.ps1; " +
               "the README.md next to it has the same text.\n";
    }

    public static void Open(IWin32Window owner, string? topic = null)
    {
        if (open is null || open.IsDisposed)
        {
            open = new HelpForm();
            open.Show(owner);
        }
        else
        {
            open.WindowState = FormWindowState.Normal;
            open.Activate();
        }
        if (topic is not null)
            open.Go(topic);
    }

    private HelpForm()
    {
        Text = "CatR-Tools documentation";
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1100, 780);
        MinimumSize = new Size(700, 480);
        BackColor = Theme.Canvas;
        Font = Theme.Body;
        KeyPreview = true;

        text.BackColor = Theme.Surface;
        text.ForeColor = Theme.Ink;
        topics.BackColor = Theme.Surface;
        topics.ForeColor = Theme.Ink;
        topics.DrawItem += DrawTopic;
        topics.SelectedIndexChanged += (_, _) =>
        {
            if (topics.SelectedIndex >= 0 && topics.Focused)
                ScrollTo(sections[topics.SelectedIndex].Start);
        };
        text.LinkClicked += (_, link) =>
        {
            if (link.LinkText is { } url && url.StartsWith("http", StringComparison.OrdinalIgnoreCase))
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
        };
        search.Dock = DockStyle.Fill;
        search.TextChanged += (_, _) => { searchFrom = 0; Find(); };
        search.KeyDown += (_, key) =>
        {
            if (key.KeyCode == Keys.Enter) { key.SuppressKeyPress = true; Find(); }
        };
        KeyDown += (_, key) =>
        {
            if (key.KeyCode == Keys.Escape) Close();
            if (key.Control && key.KeyCode == Keys.F) { search.Focus(); search.SelectAll(); }
        };

        var left = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1, Padding = new Padding(12), BackColor = Theme.Surface };
        left.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        left.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        left.Controls.Add(search, 0, 0);
        left.Controls.Add(topics, 0, 1);
        var right = new Panel { Dock = DockStyle.Fill, Padding = new Padding(24, 16, 12, 12), BackColor = Theme.Surface };
        right.Controls.Add(text);
        var split = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, BackColor = Theme.Line, Padding = Padding.Empty };
        split.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 260));
        split.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        left.Margin = new Padding(0, 0, 1, 0);
        right.Margin = Padding.Empty;
        split.Controls.Add(left, 0, 0);
        split.Controls.Add(right, 1, 0);
        Controls.Add(split);
        Theme.Style(this);
        Theme.TitleBar(this);
        text.BackColor = Theme.Surface;     // Style gives text boxes the input-field colour; this one is a page

        Render(Markdown());
    }

    private void DrawTopic(object? sender, DrawItemEventArgs e)
    {
        if (e.Index < 0) return;
        var (title, level, _) = sections[e.Index];
        var selected = (e.State & DrawItemState.Selected) != 0;
        using var back = new SolidBrush(selected ? Theme.AccentSoft : Theme.Surface);
        e.Graphics.FillRectangle(back, e.Bounds);
        var font = level <= 2 ? Theme.BodyBold : Theme.Body;
        var indent = level <= 2 ? 6 : 20;
        TextRenderer.DrawText(e.Graphics, title, font, new Rectangle(e.Bounds.X + indent, e.Bounds.Y, e.Bounds.Width - indent, e.Bounds.Height),
            level <= 2 ? Theme.Ink : Theme.Muted, TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
    }

    /// <summary>Jumps to the first section whose title starts with <paramref name="topic"/>.</summary>
    private void Go(string topic)
    {
        // An exact title at the highest level first (## PCFS, not the ### PCFS page), then a prefix.
        var index = sections.Select((section, at) => (section, at))
            .Where(item => string.Equals(item.section.Title, topic, StringComparison.OrdinalIgnoreCase))
            .OrderBy(item => item.section.Level).Select(item => item.at).DefaultIfEmpty(-1).First();
        if (index < 0)
            index = sections.FindIndex(section => section.Title.StartsWith(topic, StringComparison.OrdinalIgnoreCase));
        if (index < 0) return;
        topics.SelectedIndex = index;
        ScrollTo(sections[index].Start);
    }

    private void ScrollTo(int position)
    {
        text.SelectionStart = text.TextLength;     // scroll past it first, so the heading lands at the top
        text.ScrollToCaret();
        text.SelectionStart = position;
        text.SelectionLength = 0;
        text.ScrollToCaret();
    }

    private void Find()
    {
        var query = search.Text.Trim();
        if (query.Length == 0) return;
        var at = text.Find(query, searchFrom, RichTextBoxFinds.None);
        if (at < 0 && searchFrom > 0)
            at = text.Find(query, 0, RichTextBoxFinds.None);      // wrap around
        if (at < 0) { search.ForeColor = Theme.Bad; return; }
        search.ForeColor = Theme.Ink;
        text.ScrollToCaret();
        searchFrom = at + query.Length;
        var section = sections.FindLastIndex(s => s.Start <= at);
        if (section >= 0) topics.SelectedIndex = section;
    }

    // ---- A small Markdown renderer: headings, paragraphs, bullets, code blocks, `code`, **bold**, tables. ----

    private void Render(string markdown)
    {
        text.Clear();
        var lines = markdown.Replace("\r\n", "\n").Split('\n');
        var paragraph = new List<string>();
        for (var i = 0; i < lines.Length; i++)
        {
            var line = lines[i];
            if (line.StartsWith("```"))
            {
                Flush(paragraph);
                var code = new List<string>();
                for (i++; i < lines.Length && !lines[i].StartsWith("```"); i++)
                    code.Add(lines[i]);
                Append(string.Join("\n", code) + "\n\n", Theme.Mono, Theme.Ink, indent: 18);
                continue;
            }
            if (HeadingRegex().Match(line) is { Success: true } heading)
            {
                Flush(paragraph);
                var level = heading.Groups[1].Length;
                var title = heading.Groups[2].Value.Trim();
                sections.Add((title, level, text.TextLength));
                topics.Items.Add(title);
                var font = level == 1 ? Theme.PageTitle : level == 2 ? Theme.CardTitle : Theme.BodyBold;
                if (text.TextLength > 0) Append("\n", Theme.Small, Theme.Ink);
                Append(title + "\n", font, Theme.Ink);
                Append("\n", Theme.Small, Theme.Ink);
                continue;
            }
            if (line.StartsWith('>'))
            {
                Flush(paragraph);
                var quote = new List<string>();
                for (; i < lines.Length && lines[i].StartsWith('>'); i++)
                    quote.Add(lines[i].TrimStart('>').Trim());
                i--;
                Inline(string.Join(" ", quote) + "\n\n", 24);
                continue;
            }
            if (line.StartsWith('|'))
            {
                Flush(paragraph);
                for (; i < lines.Length && lines[i].StartsWith('|'); i++)
                {
                    if (TableRuleRegex().IsMatch(lines[i])) continue;
                    var cells = lines[i].Trim().Trim('|').Split('|').Select(cell => cell.Trim()).ToArray();
                    Inline("  " + string.Join("   ·   ", cells) + "\n", 0);
                }
                i--;
                Append("\n", Theme.Small, Theme.Ink);
                continue;
            }
            if (BulletRegex().Match(line) is { Success: true } bullet)
            {
                Flush(paragraph);
                var depth = bullet.Groups[1].Length / 2;
                var marker = bullet.Groups[2].Value is "-" or "*" ? "•" : bullet.Groups[2].Value;
                var item = new List<string> { bullet.Groups[3].Value };
                while (i + 1 < lines.Length && lines[i + 1].StartsWith("  ") && !BulletRegex().IsMatch(lines[i + 1]))
                    item.Add(lines[++i].Trim());
                Inline(marker + "  " + string.Join(" ", item) + "\n", 14 + depth * 18, hanging: marker.Length > 1 ? 18 : 14);
                if (i + 1 >= lines.Length || !BulletRegex().IsMatch(lines[i + 1]) && lines[i + 1].Trim().Length == 0)
                    Append("\n", Theme.Small, Theme.Ink);
                continue;
            }
            if (line.Trim().Length == 0)
            {
                Flush(paragraph);
                continue;
            }
            paragraph.Add(line.Trim());
        }
        Flush(paragraph);
        text.SelectionStart = 0;
        text.ScrollToCaret();
    }

    private void Flush(List<string> paragraph)
    {
        if (paragraph.Count == 0) return;
        Inline(string.Join(" ", paragraph) + "\n\n", 0);
        paragraph.Clear();
    }

    /// <summary>Text with `code` and **bold** spans.</summary>
    private void Inline(string value, int indent, int hanging = 0)
    {
        foreach (var part in SpanRegex().Split(value))
        {
            if (part.Length == 0) continue;
            if (part.Length > 2 && part.StartsWith('`') && part.EndsWith('`'))
                Append(part[1..^1], Theme.Mono, Theme.Accent, indent, hanging);
            else if (part.Length > 4 && part.StartsWith("**") && part.EndsWith("**"))
                Append(part[2..^2], Theme.BodyBold, Theme.Ink, indent, hanging);
            else
                Append(part.Replace("\\\\", "\\"), Theme.Body, Theme.Ink, indent, hanging);
        }
    }

    private void Append(string value, Font font, Color color, int indent = 0, int hanging = 0)
    {
        // Format the range after appending it: formatting set on an empty selection doesn't always stick.
        var start = text.TextLength;
        text.AppendText(value);
        text.Select(start, text.TextLength - start);
        text.SelectionFont = font;
        text.SelectionColor = color;
        text.SelectionIndent = indent;
        text.SelectionHangingIndent = hanging;
        text.Select(text.TextLength, 0);
    }

    [GeneratedRegex(@"^(#{1,4})\s+(.+)$")]
    private static partial Regex HeadingRegex();

    [GeneratedRegex(@"^(\s*)([-*]|\d+\.)\s+(.*)$")]
    private static partial Regex BulletRegex();

    [GeneratedRegex(@"^\|[\s:|-]+\|$")]
    private static partial Regex TableRuleRegex();

    [GeneratedRegex(@"(`[^`]+`|\*\*[^*]+\*\*)")]
    private static partial Regex SpanRegex();
}
