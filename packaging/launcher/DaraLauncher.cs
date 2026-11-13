// DARA.exe: owns the DARA window and shows the web UI in an embedded WebView2.
//
// Layout next to this exe (built by packaging\build_dist.ps1):
//   python\python.exe   bundled CPython with every dependency (dara found via dara-src.pth)
//   src\dara            DARA sources; BGMN is downloaded into src\dara\bgmn\BGMNwin on first start
//   scripts\            the same helper scripts START.cmd uses
// The Python side is started exactly as START does it (scripts\launch_dara_web.py), inside a job
// object so that closing the window stops the server, its analysis processes and Ray.
//
// C# 5 only: it is compiled by the .NET Framework csc.exe that ships with Windows.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Dara.Desktop
{
    static class Program
    {
        // Also used by the installer (AppMutex) to ask the user to close DARA before an upgrade.
        public const string MutexName = "DARA-Desktop-SingleInstance";
        public static readonly int ShowMessage = Native.RegisterWindowMessage("DARA-Desktop-Show");

        [STAThread]
        static int Main(string[] args)
        {
            bool created;
            using (var mutex = new Mutex(true, MutexName, out created))
            {
                if (!created)
                {
                    // Second launch: bring the running window to the front and exit.
                    Native.AllowSetForegroundWindow(-1);
                    Native.PostMessage((IntPtr)0xffff, ShowMessage, IntPtr.Zero, IntPtr.Zero);
                    return 0;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MainForm(args.Contains("--set-api-key")));
                GC.KeepAlive(mutex);
            }
            return 0;
        }
    }

    class StartupException : Exception
    {
        public readonly string Details;
        public StartupException(string message, string details) : base(message) { Details = details; }
    }

    class MainForm : Form
    {
        const int DefaultPort = 8898;
        const int StartupTimeoutSeconds = 300;
        static readonly string[] IsolatedVariables = { "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONSTARTUP" };

        readonly string appDir = Path.GetDirectoryName(Application.ExecutablePath);
        readonly string dataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DARA");
        readonly bool forceKeyPrompt;
        readonly Dictionary<string, string> pythonEnvironment = new Dictionary<string, string>();

        readonly Panel splash = new Panel();
        readonly Label title = new Label();
        readonly Label status = new Label();
        readonly ProgressBar progress = new ProgressBar();
        readonly TextBox details = new TextBox();
        readonly FlowLayoutPanel buttons = new FlowLayoutPanel();
        readonly Button retryButton = new Button();
        readonly Button browserButton = new Button();
        readonly Button logButton = new Button();
        readonly Button closeButton = new Button();
        readonly WebView2 webView = new WebView2();

        Task webViewReady;
        bool useBrowser;          // WebView2 runtime missing: show DARA in the default browser
        bool starting;
        bool closing;
        bool closeConfirmed;
        bool serverReady;
        bool shownOnce;
        bool attachToRunningServer;
        IntPtr job = IntPtr.Zero;
        Process server;           // python scripts\launch_dara_web.py (supervises the uvicorn server)
        int port;
        string appUrl;
        string serverLog;
        string lastSaveFolder;
        readonly StringBuilder serverOutput = new StringBuilder();

        public MainForm(bool forceKeyPrompt)
        {
            this.forceKeyPrompt = forceKeyPrompt;
            Text = "DARA";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            MinimumSize = new Size(800, 560);
            StartPosition = FormStartPosition.CenterScreen;
            SuspendLayout();
            // Bounds below are in 96-DPI units; WinForms scales them to the monitor DPI.
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            BackColor = Color.FromArgb(245, 248, 252);

            webView.Dock = DockStyle.Fill;
            webView.DefaultBackgroundColor = BackColor;
            Controls.Add(webView);

            splash.Dock = DockStyle.Fill;
            splash.Padding = new Padding(60, 50, 60, 30);
            splash.BackColor = BackColor;
            title.Text = "Starting DARA…";
            title.Font = new Font("Segoe UI", 20f, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(29, 66, 69);
            title.AutoSize = true;
            title.Location = new Point(60, 50);
            status.Font = new Font("Segoe UI", 11f);
            status.ForeColor = Color.FromArgb(23, 43, 77);
            status.AutoSize = false;
            status.SetBounds(62, 105, 900, 50);
            status.Anchor = AnchorStyles.Left | AnchorStyles.Top | AnchorStyles.Right;
            progress.Style = ProgressBarStyle.Marquee;
            progress.SetBounds(62, 160, 420, 18);
            details.Multiline = true;
            details.ReadOnly = true;
            details.ScrollBars = ScrollBars.Both;
            details.WordWrap = false;
            details.Font = new Font("Consolas", 9f);
            details.SetBounds(62, 195, 900, 360);
            details.Anchor = AnchorStyles.Left | AnchorStyles.Top | AnchorStyles.Right | AnchorStyles.Bottom;
            details.Visible = false;
            buttons.FlowDirection = FlowDirection.LeftToRight;
            buttons.SetBounds(60, 570, 900, 45);
            buttons.Anchor = AnchorStyles.Left | AnchorStyles.Bottom | AnchorStyles.Right;
            buttons.Visible = false;
            SetButton(retryButton, "Try again", delegate { StartDara(); });
            SetButton(browserButton, "Open in browser", delegate { OpenExternal(appUrl); });
            SetButton(logButton, "Open log folder", delegate { OpenLogFolder(); });
            SetButton(closeButton, "Close", delegate { Close(); });
            splash.Controls.AddRange(new Control[] { title, status, progress, details, buttons });
            Controls.Add(splash);
            splash.BringToFront();
            splash.Resize += delegate
            {
                int width = Math.Max(300, splash.ClientSize.Width - 2 * status.Left);
                status.Width = width;
                details.Width = width;
                buttons.Width = width;
                details.Height = Math.Max(80, splash.ClientSize.Height - details.Top - 2 * buttons.Height);
                buttons.Top = details.Bottom + buttons.Height / 3;
            };
            ResumeLayout(false);
        }

        void SetButton(Button button, string text, EventHandler onClick)
        {
            button.Text = text;
            button.AutoSize = true;
            button.Padding = new Padding(8, 3, 8, 3);
            button.Font = new Font("Segoe UI", 10f);
            button.Click += onClick;
            buttons.Controls.Add(button);
        }

        // ---------------------------------------------------------------------------------------
        // Startup
        // ---------------------------------------------------------------------------------------

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            // After DPI scaling: 90 % of the screen, at most about 1500 x 950 logical pixels.
            var area = Screen.FromControl(this).WorkingArea;
            float scale = DeviceDpi / 96f;
            Size = new Size(Math.Min((int)(1500 * scale), area.Width * 9 / 10), Math.Min((int)(950 * scale), area.Height * 9 / 10));
            CenterToScreen();
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            Log("DARA.exe started from " + appDir);
            if (!CheckWebView2Runtime()) return;
            if (!useBrowser) webViewReady = InitializeWebView();
            StartDara();
        }

        bool CheckWebView2Runtime()
        {
            string version = null;
            try { version = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
            catch (Exception error) { Log("WebView2 runtime check failed: " + error.Message); }
            if (!string.IsNullOrEmpty(version)) { Log("WebView2 runtime " + version); return true; }
            var answer = MessageBox.Show(this,
                "DARA shows its window with the Microsoft Edge WebView2 Runtime, which is not installed on this PC.\n\n" +
                "Yes: open the download page (install the Evergreen Runtime, then start DARA again).\n" +
                "No: use DARA in your default web browser this time.",
                "DARA - WebView2 Runtime missing", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Warning);
            if (answer == DialogResult.Yes)
            {
                OpenExternal("https://developer.microsoft.com/microsoft-edge/webview2/#download");
                Close();
                return false;
            }
            if (answer != DialogResult.No) { Close(); return false; }
            useBrowser = true;
            return true;
        }

        async Task InitializeWebView()
        {
            var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(dataDir, "WebView2"), null);
            await webView.EnsureCoreWebView2Async(environment);
            var core = webView.CoreWebView2;
            core.Settings.IsStatusBarEnabled = false;
            core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
            {
                e.Handled = true;   // target=_blank and window.open go to the default browser
                OpenExternal(e.Uri);
            };
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            {
                if (IsAppUri(e.Uri)) return;
                e.Cancel = true;
                OpenExternal(e.Uri);
            };
            core.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                if (shownOnce || appUrl == null || starting) return;
                if (!e.IsSuccess)
                {
                    ShowMessagePanel("DARA could not open", "The DARA page did not load (" + e.WebErrorStatus + ").", retryButton, logButton, closeButton);
                    return;
                }
                shownOnce = true;
                splash.Visible = false;
                Log(string.Format("Window ready {0:F1} s after DARA.exe started",
                    (DateTime.Now - Process.GetCurrentProcess().StartTime).TotalSeconds));
            };
            core.DocumentTitleChanged += delegate
            {
                string text = core.DocumentTitle;
                Text = string.IsNullOrEmpty(text) || text.StartsWith("127.0.0.1") ? "DARA" : text;
            };
            core.DownloadStarting += OnDownloadStarting;
            core.ProcessFailed += delegate(object sender, CoreWebView2ProcessFailedEventArgs e)
            {
                Log("WebView2 process failed: " + e.ProcessFailedKind);
                if (e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessExited ||
                    e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessUnresponsive)
                {
                    try { core.Reload(); } catch (Exception) { }
                }
            };
        }

        async void StartDara()
        {
            if (starting || closing) return;
            starting = true;
            serverReady = false;
            shownOnce = false;
            ShowProgress("Starting DARA…", "Preparing…");
            try
            {
                StopServer();
                job = Native.CreateKillOnCloseJob();
                await AskMaterialsProjectKey();

                port = await Task.Run(() => ChoosePort());
                appUrl = string.Format("http://127.0.0.1:{0}/", port);
                if (attachToRunningServer)
                {
                    // Another DARA (for example START.cmd) already serves this data; a second server
                    // on the same database would mark its running analyses as failed.
                    Log("Using the DARA server that is already running at " + appUrl);
                }
                else
                {
                    status.Text = "Checking certificates…";
                    var ca = await RunPython(Quote(Path.Combine(appDir, "scripts", "windows_ca_bundle.py")) + " --repo-root " + Quote(appDir), null);
                    string bundle = ca.Item2.Split('\n').Select(line => line.Trim()).LastOrDefault(line => line.EndsWith(".pem") && File.Exists(line));
                    if (ca.Item1 == 0 && bundle != null)
                        foreach (var name in new[] { "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE" }) pythonEnvironment[name] = bundle;

                    status.Text = "Checking the BGMN refinement programs…";
                    var bgmn = await RunPython(Quote(Path.Combine(appDir, "scripts", "ensure_bgmn.py")), OnBgmnLine);
                    if (bgmn.Item1 != 0)
                        throw new StartupException("The BGMN refinement programs could not be downloaded.",
                            "DARA downloads BGMN (about 7 MB) from cedergrouphub.github.io the first time it starts. " +
                            "Check the internet connection, proxy or firewall, then choose Try again.\r\n\r\n" + bgmn.Item2);
                    progress.Style = ProgressBarStyle.Marquee;
                    await StartServer();
                }

                if (useBrowser)
                {
                    OpenExternal(appUrl);
                    ShowMessagePanel("DARA is open in your web browser",
                        "Address: " + appUrl + "\r\nKeep this window open while you use DARA. Closing it stops DARA.",
                        browserButton, closeButton);
                    return;
                }
                status.Text = "Opening DARA…";
                await webViewReady;
                webView.CoreWebView2.Navigate(appUrl);
            }
            catch (StartupException error)
            {
                Log("Startup failed: " + error.Message + "\r\n" + error.Details);
                if (!closing) ShowMessagePanel("DARA could not start", error.Message + "\r\n\r\n" + error.Details, retryButton, logButton, closeButton);
            }
            catch (Exception error)
            {
                Log("Startup failed: " + error);
                if (!closing) ShowMessagePanel("DARA could not start", error.Message + "\r\n\r\n" + error, retryButton, logButton, closeButton);
            }
            finally
            {
                starting = false;
            }
        }

        void OnBgmnLine(string line)
        {
            var match = Regex.Match(line, @"BGMN download: (\d+)%");
            if (match.Success)
            {
                progress.Style = ProgressBarStyle.Continuous;
                progress.Value = Math.Min(100, int.Parse(match.Groups[1].Value));
                status.Text = "Downloading the BGMN refinement programs (first start only)… " + match.Groups[1].Value + " %";
            }
            else if (line.StartsWith("BGMN programs missing"))
            {
                status.Text = "Downloading the BGMN refinement programs (first start only)…";
            }
        }

        async Task AskMaterialsProjectKey()
        {
            // Same dialog and rules as START.cmd (scripts\materials_project_key.ps1).
            string key = Environment.GetEnvironmentVariable("MP_API_KEY", EnvironmentVariableTarget.User);
            bool skipped = File.Exists(Path.Combine(dataDir, "mp-api-setup-skipped"));
            bool hasKey = !string.IsNullOrWhiteSpace(key) || !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("MP_API_KEY"));
            if (forceKeyPrompt || (!hasKey && !skipped))
            {
                status.Text = "Waiting for the Materials Project API key dialog…";
                string script = Path.Combine(appDir, "scripts", "materials_project_key.ps1").Replace("'", "''");
                var info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
                    "-STA -NoLogo -NoProfile -ExecutionPolicy Bypass -Command \"& { . '" + script + "'; [void](Initialize-MaterialsProjectKey" +
                    (forceKeyPrompt ? " -Force" : "") + ") }\"");
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                using (var dialog = Process.Start(info))
                {
                    Native.AllowSetForegroundWindow(dialog.Id);
                    await Task.Run(() => dialog.WaitForExit());
                }
                key = Environment.GetEnvironmentVariable("MP_API_KEY", EnvironmentVariableTarget.User);
            }
            // Explorer can hold an environment from before the key was saved: pass the saved one on.
            if (!string.IsNullOrWhiteSpace(key)) pythonEnvironment["MP_API_KEY"] = key.Trim();
        }

        int ChoosePort()
        {
            attachToRunningServer = false;
            int preferred = DefaultPort;
            int configured;
            if (int.TryParse(Environment.GetEnvironmentVariable("DARA_SERVER_PORT"), out configured) && configured > 0 && configured < 65536)
                preferred = configured;
            if (PortIsFree(preferred)) return preferred;
            if (IsDaraServer(preferred)) { attachToRunningServer = true; return preferred; }
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            int free = ((IPEndPoint)listener.LocalEndpoint).Port;
            listener.Stop();
            Log(string.Format("Port {0} is used by another program; DARA uses port {1}.", preferred, free));
            return free;
        }

        static bool PortIsFree(int candidate)
        {
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, candidate);
                listener.ExclusiveAddressUse = true;
                listener.Start();
                listener.Stop();
                return true;
            }
            catch (SocketException) { return false; }
        }

        static bool IsDaraServer(int candidate)
        {
            if (PortIsFree(candidate)) return false;
            string body = HttpGet(string.Format("http://127.0.0.1:{0}/api/tasks?limit=1", candidate), 3000);
            return body != null && body.Contains("\"total_tasks\"");
        }

        async Task StartServer()
        {
            status.Text = "Starting the DARA server…";
            serverOutput.Clear();
            serverLog = null;
            // Window lifetime decides the server lifetime here, not the browser heartbeat.
            pythonEnvironment["DARA_IDLE_SHUTDOWN"] = "0";
            pythonEnvironment["DARA_SERVER_HOST"] = "127.0.0.1";
            PruneServerLogs();
            server = StartPython("-u " + Quote(Path.Combine(appDir, "scripts", "launch_dara_web.py")) +
                " --no-browser --port " + port + " --timeout " + StartupTimeoutSeconds, delegate(string line)
                {
                    lock (serverOutput) serverOutput.AppendLine(line);
                    var match = Regex.Match(line, @"[Ll]og: (.+?\.log)");
                    if (match.Success) serverLog = match.Groups[1].Value;
                }, true);
            var current = server;
            current.EnableRaisingEvents = true;
            current.Exited += delegate { BeginInvokeSafe(delegate { if (current == server) OnServerExited(); }); };
            var clock = Stopwatch.StartNew();
            while (true)
            {
                if (await Task.Run(() => HttpGet(appUrl, 1500)) != null) break;
                if (server.HasExited)
                    throw new StartupException("The DARA server could not start.", ServerDiagnostics());
                if (clock.Elapsed.TotalSeconds > StartupTimeoutSeconds + 10)
                    throw new StartupException("The DARA server did not answer within " + StartupTimeoutSeconds + " seconds.", ServerDiagnostics());
                status.Text = string.Format("Starting the DARA server… ({0:F0} s)", clock.Elapsed.TotalSeconds);
                await Task.Delay(400);
            }
            serverReady = true;
            Log(string.Format("Server ready at {0} after {1:F1} s", appUrl, clock.Elapsed.TotalSeconds));
        }

        void OnServerExited()
        {
            if (closing || !serverReady) return;   // startup failures are reported by StartServer
            serverReady = false;
            Log("The DARA server stopped (exit code " + SafeExitCode(server) + ").");
            ShowMessagePanel("The DARA server stopped",
                "The DARA server stopped unexpectedly. Choose Try again to restart it.\r\n\r\n" + ServerDiagnostics(),
                retryButton, logButton, closeButton);
        }

        string ServerDiagnostics()
        {
            string text;
            lock (serverOutput) text = serverOutput.ToString().Trim();
            if (serverLog != null && File.Exists(serverLog))
            {
                try
                {
                    var lines = ReadShared(serverLog).Split('\n');
                    text += "\r\n\r\nLast lines of " + serverLog + ":\r\n" + string.Join("\n", lines.Skip(Math.Max(0, lines.Length - 40)));
                }
                catch (IOException) { }
            }
            return text.Replace("\r\n", "\n").Replace("\n", "\r\n");
        }

        void PruneServerLogs()
        {
            // launch_dara_web.py writes one log per start; keep the newest 10.
            try
            {
                var folder = new DirectoryInfo(Path.Combine(appDir, "artifacts", "launcher"));
                if (!folder.Exists) return;
                foreach (var old in folder.GetFiles("server-*.log").OrderByDescending(f => f.Name).Skip(10)) old.Delete();
            }
            catch (Exception) { }
        }

        // ---------------------------------------------------------------------------------------
        // Python processes (all inside the kill-on-close job)
        // ---------------------------------------------------------------------------------------

        Process StartPython(string arguments, Action<string> onLine, bool logLines)
        {
            var info = new ProcessStartInfo(Path.Combine(appDir, "python", "python.exe"), arguments);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;       // children (jobs, Ray) inherit the hidden console
            info.WorkingDirectory = appDir;
            info.RedirectStandardInput = true;   // launch_dara_web.py waits for Enter after a failure
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            foreach (var name in IsolatedVariables) info.EnvironmentVariables.Remove(name);
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            foreach (var pair in pythonEnvironment) info.EnvironmentVariables[pair.Key] = pair.Value;
            var process = new Process();
            process.StartInfo = info;
            DataReceivedEventHandler handler = delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                if (logLines) Log("  " + e.Data);
                if (onLine != null) BeginInvokeSafe(delegate { onLine(e.Data); });
            };
            process.OutputDataReceived += handler;
            process.ErrorDataReceived += handler;
            process.Start();
            // shortcut: assigned right after start; Python needs far longer than this to spawn children.
            if (!Native.AssignProcessToJobObject(job, process.Handle))
                Log("Could not add the Python process to the job object (error " + Marshal.GetLastWin32Error() + ").");
            process.StandardInput.Close();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            return process;
        }

        async Task<Tuple<int, string>> RunPython(string script, Action<string> onLine)
        {
            var output = new StringBuilder();
            var process = StartPython("-I " + script, delegate(string line)
            {
                output.AppendLine(line);
                if (onLine != null) onLine(line);
            }, true);
            await Task.Run(() => process.WaitForExit());
            await Task.Delay(50);   // let the queued output callbacks run
            int code = process.ExitCode;
            process.Dispose();
            return Tuple.Create(code, output.ToString());
        }

        void StopServer()
        {
            serverReady = false;
            if (job != IntPtr.Zero)
            {
                Native.CloseHandle(job);   // kill-on-close: the server, its workers and Ray stop now
                job = IntPtr.Zero;
            }
            server = null;
        }

        // ---------------------------------------------------------------------------------------
        // Window behaviour
        // ---------------------------------------------------------------------------------------

        bool IsAppUri(string uri)
        {
            if (uri == null) return true;
            if (uri.StartsWith("about:") || uri.StartsWith("blob:") || uri.StartsWith("data:")) return true;
            return appUrl != null && uri.StartsWith(appUrl.TrimEnd('/'), StringComparison.OrdinalIgnoreCase);
        }

        void OpenExternal(string uri)
        {
            Uri parsed;
            if (!Uri.TryCreate(uri, UriKind.Absolute, out parsed)) return;
            if (parsed.Scheme != Uri.UriSchemeHttp && parsed.Scheme != Uri.UriSchemeHttps && parsed.Scheme != Uri.UriSchemeMailto) return;
            try { Process.Start(new ProcessStartInfo(parsed.AbsoluteUri) { UseShellExecute = true }); }
            catch (Exception error) { MessageBox.Show(this, "Could not open " + parsed.AbsoluteUri + ": " + error.Message, "DARA"); }
        }

        void OnDownloadStarting(object sender, CoreWebView2DownloadStartingEventArgs e)
        {
            var deferral = e.GetDeferral();
            BeginInvoke(new Action(delegate
            {
                try
                {
                    using (var dialog = new SaveFileDialog())
                    {
                        string suggested = Path.GetFileName(e.ResultFilePath);
                        string extension = Path.GetExtension(suggested);
                        dialog.FileName = suggested;
                        dialog.InitialDirectory = lastSaveFolder ?? Path.GetDirectoryName(e.ResultFilePath);
                        dialog.Filter = extension.Length > 1
                            ? string.Format("{0} file (*{1})|*{1}|All files (*.*)|*.*", extension.Substring(1).ToUpperInvariant(), extension)
                            : "All files (*.*)|*.*";
                        dialog.OverwritePrompt = true;
                        if (dialog.ShowDialog(this) == DialogResult.OK)
                        {
                            if (File.Exists(dialog.FileName)) File.Delete(dialog.FileName);   // the user confirmed replacing it
                            e.ResultFilePath = dialog.FileName;
                            e.Handled = true;   // no download flyout: the user picked the place
                            lastSaveFolder = Path.GetDirectoryName(dialog.FileName);
                            var operation = e.DownloadOperation;
                            operation.StateChanged += delegate
                            {
                                if (operation.State == CoreWebView2DownloadState.Interrupted)
                                    MessageBox.Show(this, "Saving " + dialog.FileName + " failed: " + operation.InterruptReason, "DARA");
                                else if (operation.State == CoreWebView2DownloadState.Completed)
                                    Log("Saved " + operation.ResultFilePath);
                            };
                        }
                        else
                        {
                            e.Cancel = true;
                        }
                    }
                }
                catch (Exception error)
                {
                    e.Cancel = true;
                    MessageBox.Show(this, "Could not save the file: " + error.Message, "DARA");
                }
                finally
                {
                    deferral.Complete();
                }
            }));
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!closeConfirmed && serverReady && server != null && !server.HasExited && e.CloseReason != CloseReason.WindowsShutDown)
            {
                int active = ActiveAnalyses();
                if (active > 0)
                {
                    var answer = MessageBox.Show(this,
                        string.Format("{0} analysis task(s) are still running or waiting in the queue.\n\n" +
                                      "Closing DARA stops them; they are marked as failed and can be run again later.\n\nClose DARA anyway?", active),
                        "DARA - Analysis running", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
                    if (answer != DialogResult.Yes) { e.Cancel = true; return; }
                }
            }
            closeConfirmed = true;
            closing = true;
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            StopServer();
            Log("DARA.exe closed.");
            base.OnFormClosed(e);
        }

        int ActiveAnalyses()
        {
            string body = HttpGet(appUrl + "api/tasks?limit=1&state=READY,RUNNING,CANCELLING", 3000);
            if (body == null) return 0;
            var match = Regex.Match(body, "\"total_tasks\"\\s*:\\s*(\\d+)");
            return match.Success ? int.Parse(match.Groups[1].Value) : 0;
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == Program.ShowMessage && Program.ShowMessage != 0)
            {
                if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                Show();
                Activate();
                Native.SetForegroundWindow(Handle);
                return;
            }
            base.WndProc(ref m);
        }

        // ---------------------------------------------------------------------------------------
        // Helpers
        // ---------------------------------------------------------------------------------------

        void ShowProgress(string heading, string text)
        {
            title.Text = heading;
            status.Text = text;
            progress.Style = ProgressBarStyle.Marquee;
            progress.Visible = true;
            details.Visible = false;
            buttons.Visible = false;
            splash.Visible = true;
            splash.BringToFront();
        }

        void ShowMessagePanel(string heading, string text, params Button[] visibleButtons)
        {
            title.Text = heading;
            status.Text = "";
            progress.Visible = false;
            details.Text = text;
            details.Visible = true;
            foreach (Control button in buttons.Controls) button.Visible = Array.IndexOf(visibleButtons, button) >= 0;
            buttons.Visible = true;
            splash.Visible = true;
            splash.BringToFront();
        }

        void OpenLogFolder()
        {
            string folder = serverLog != null ? Path.GetDirectoryName(serverLog) : Path.Combine(dataDir, "logs");
            if (!Directory.Exists(folder)) folder = Path.Combine(dataDir, "logs");
            try { Process.Start(new ProcessStartInfo("explorer.exe", Quote(folder))); } catch (Exception) { }
        }

        void BeginInvokeSafe(Action action)
        {
            if (IsDisposed || !IsHandleCreated) return;
            try { BeginInvoke(action); } catch (InvalidOperationException) { }
        }

        static string SafeExitCode(Process process)
        {
            try { return process.ExitCode.ToString(); } catch (Exception) { return "?"; }
        }

        static string Quote(string value)
        {
            return "\"" + value + "\"";
        }

        static string ReadShared(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var reader = new StreamReader(stream, Encoding.UTF8))
                return reader.ReadToEnd();
        }

        static string HttpGet(string url, int timeoutMs)
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(url);
                request.Proxy = null;   // never send local requests through a corporate proxy
                request.Timeout = timeoutMs;
                request.ReadWriteTimeout = timeoutMs;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                    return reader.ReadToEnd();
            }
            catch (WebException) { return null; }
            catch (IOException) { return null; }
        }

        readonly object logLock = new object();

        void Log(string message)
        {
            try
            {
                string folder = Path.Combine(dataDir, "logs");
                Directory.CreateDirectory(folder);
                string path = Path.Combine(folder, "launcher.log");
                lock (logLock)
                {
                    var info = new FileInfo(path);
                    if (info.Exists && info.Length > 2 * 1024 * 1024) File.Delete(path);
                    File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + "\r\n", new UTF8Encoding(false));
                }
            }
            catch (Exception) { }   // logging must never stop DARA
        }
    }

    static class Native
    {
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int RegisterWindowMessage(string name);
        [DllImport("user32.dll")]
        public static extern bool PostMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")]
        public static extern bool AllowSetForegroundWindow(int processId);
        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr window);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimitInformation info, int size);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);

        [StructLayout(LayoutKind.Sequential)]
        struct BasicLimitInformation
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct IoCounters
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct ExtendedLimitInformation
        {
            public BasicLimitInformation BasicLimitInformation;
            public IoCounters IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        // Every process in the job (and the processes they start) is killed when the last
        // handle closes: when DARA.exe closes the window, exits or crashes.
        public static IntPtr CreateKillOnCloseJob()
        {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new InvalidOperationException("CreateJobObject failed (" + Marshal.GetLastWin32Error() + ").");
            var info = new ExtendedLimitInformation();
            info.BasicLimitInformation.LimitFlags = 0x2000;   // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(job, 9, ref info, Marshal.SizeOf(typeof(ExtendedLimitInformation))))
                throw new InvalidOperationException("SetInformationJobObject failed (" + Marshal.GetLastWin32Error() + ").");
            return job;
        }
    }
}
