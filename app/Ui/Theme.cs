using System.Runtime.InteropServices;

namespace CatRTools;

/// <summary>Colors and fonts, created once and shared. (The POC made new Font objects on every
/// page rebuild and never disposed them.) The palette is chosen once at start: Light or Dark,
/// or System (Windows' app mode).</summary>
internal static class Theme
{
    public static bool Dark { get; private set; }

    public static Color Ink { get; private set; }
    public static Color Muted { get; private set; }
    public static Color Canvas { get; private set; }
    public static Color Surface { get; private set; }
    public static Color Line { get; private set; }
    public static Color Field { get; private set; }
    public static Color Accent { get; private set; }
    public static Color AccentSoft { get; private set; }
    public static Color Banner { get; private set; }
    public static Color BannerText { get; private set; }
    public static Color Slate { get; private set; }
    public static Color Terminal { get; private set; }
    public static Color TerminalText { get; private set; }
    public static Color TerminalDim { get; private set; }
    public static Color TerminalCommand { get; private set; }
    public static Color TerminalError { get; private set; }
    public static Color Good { get; private set; }
    public static Color Bad { get; private set; }
    public static Color Warn { get; private set; }

    public static readonly Font Body = new("Segoe UI", 9.5F);
    public static readonly Font BodyBold = new("Segoe UI Semibold", 9.5F, FontStyle.Bold);
    public static readonly Font Small = new("Segoe UI", 8.5F);
    public static readonly Font Caption = new("Segoe UI", 8F, FontStyle.Bold);
    public static readonly Font Brand = new("Segoe UI Semibold", 16F, FontStyle.Bold);
    public static readonly Font PageTitle = new("Segoe UI Semibold", 17F, FontStyle.Bold);
    public static readonly Font CardTitle = new("Segoe UI Semibold", 12F, FontStyle.Bold);
    public static readonly Font Value = new("Segoe UI Semibold", 12F, FontStyle.Bold);
    public static readonly Font Mono = new("Consolas", 9.5F);
    public static readonly Font MonoSmall = new("Consolas", 8.5F);

    static Theme() => Use(false);

    /// <summary>"Light", "Dark" or "System". Call before any window is created.</summary>
    public static void Apply(string? mode)
    {
        var dark = mode switch
        {
            "Dark" => true,
            "Light" => false,
            _ => WindowsAppsUseDark()
        };
#pragma warning disable WFO5001   // dark common controls (scrollbars, title bars, menus) in WinForms
        Application.SetColorMode(dark ? SystemColorMode.Dark : SystemColorMode.Classic);
#pragma warning restore WFO5001
        Use(dark);
    }

    private static void Use(bool dark)
    {
        Dark = dark;
        Terminal = Color.FromArgb(15, 23, 42);
        TerminalText = Color.FromArgb(203, 213, 225);
        TerminalDim = Color.FromArgb(100, 116, 139);
        TerminalCommand = Color.FromArgb(125, 211, 252);
        TerminalError = Color.FromArgb(252, 165, 165);
        if (dark)
        {
            Ink = Color.FromArgb(226, 232, 240);
            Muted = Color.FromArgb(148, 163, 184);
            Canvas = Color.FromArgb(17, 24, 39);
            Surface = Color.FromArgb(30, 41, 59);
            Line = Color.FromArgb(51, 65, 85);
            Field = Color.FromArgb(15, 23, 42);
            Accent = Color.FromArgb(59, 130, 246);
            AccentSoft = Color.FromArgb(30, 58, 110);
            Banner = Color.FromArgb(30, 58, 110);
            BannerText = Color.FromArgb(191, 219, 254);
            Slate = Color.FromArgb(203, 213, 225);
            Good = Color.FromArgb(34, 197, 94);
            Bad = Color.FromArgb(248, 113, 113);
            Warn = Color.FromArgb(251, 191, 36);
            Terminal = Color.FromArgb(11, 17, 31);
        }
        else
        {
            Ink = Color.FromArgb(30, 41, 59);
            Muted = Color.FromArgb(105, 117, 136);
            Canvas = Color.FromArgb(246, 248, 251);
            Surface = Color.White;
            Line = Color.FromArgb(226, 232, 240);
            Field = Color.FromArgb(248, 250, 252);
            Accent = Color.FromArgb(37, 99, 235);
            AccentSoft = Color.FromArgb(239, 246, 255);
            Banner = Color.FromArgb(30, 64, 112);
            BannerText = Color.FromArgb(219, 231, 248);
            Slate = Color.FromArgb(71, 85, 105);
            Good = Color.FromArgb(22, 163, 74);
            Bad = Color.FromArgb(220, 38, 38);
            Warn = Color.FromArgb(217, 119, 6);
        }
    }

    /// <summary>Windows Settings → Personalization → Colors → app mode.</summary>
    private static bool WindowsAppsUseDark()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            return key?.GetValue("AppsUseLightTheme") is int light && light == 0;
        }
        catch (System.Security.SecurityException)
        {
            return false;
        }
    }

    /// <summary>Text boxes, combo boxes, lists and grids pick up the palette (system colors otherwise).</summary>
    public static void Style(Control root)
    {
        foreach (Control control in root.Controls)
        {
            switch (control)
            {
                case TextBoxBase box when box is not OutputBox:
                    box.BackColor = Field;
                    box.ForeColor = Ink;
                    break;
                case ComboBox combo:
                    combo.BackColor = Field;
                    combo.ForeColor = Ink;
                    combo.FlatStyle = Dark ? FlatStyle.Flat : FlatStyle.Standard;
                    break;
                case ListView list:
                    list.BackColor = Surface;
                    list.ForeColor = Ink;
                    break;
                case CheckBox check:
                    check.ForeColor = Ink;
                    break;
                case MenuStrip or StatusStrip:
                    ((ToolStrip)control).Renderer = new ToolStripProfessionalRenderer(new Palette());
                    control.BackColor = Surface;
                    control.ForeColor = Ink;
                    break;
            }
            Style(control);
        }
    }

    /// <summary>Dark title bar (Windows 10 2004+ / 11).</summary>
    public static void TitleBar(Form form)
    {
        if (!Dark) return;
        form.HandleCreated += (_, _) =>
        {
            var on = 1;
            _ = DwmSetWindowAttribute(form.Handle, 20, ref on, sizeof(int));
        };
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    private sealed class Palette : ProfessionalColorTable
    {
        public override Color MenuStripGradientBegin => Surface;
        public override Color MenuStripGradientEnd => Surface;
        public override Color StatusStripGradientBegin => Surface;
        public override Color StatusStripGradientEnd => Surface;
        public override Color ToolStripDropDownBackground => Surface;
        public override Color ImageMarginGradientBegin => Surface;
        public override Color ImageMarginGradientMiddle => Surface;
        public override Color ImageMarginGradientEnd => Surface;
        public override Color MenuItemSelected => AccentSoft;
        public override Color MenuItemSelectedGradientBegin => AccentSoft;
        public override Color MenuItemSelectedGradientEnd => AccentSoft;
        public override Color MenuItemPressedGradientBegin => AccentSoft;
        public override Color MenuItemPressedGradientEnd => AccentSoft;
        public override Color MenuItemBorder => Line;
        public override Color MenuBorder => Line;
        public override Color SeparatorDark => Line;
        public override Color SeparatorLight => Line;
        public override Color ButtonSelectedHighlight => AccentSoft;
        public override Color ButtonSelectedGradientBegin => AccentSoft;
        public override Color ButtonSelectedGradientEnd => AccentSoft;
    }
}
