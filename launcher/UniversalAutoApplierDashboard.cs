using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Threading;
using System.Windows.Forms;

internal static class UniversalAutoApplierDashboard
{
    private const int Port = 18742;
    private static readonly string DashboardUrl = "http://127.0.0.1:" + Port;

    [STAThread]
    private static void Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(root, "dashboard_server.py");
            string log = Path.Combine(root, ".uaa_data", "dashboard_server.log");
            if (!File.Exists(python) || !File.Exists(script))
            {
                ShowError("The Python environment or dashboard launcher is missing. Run the README setup first.");
                return;
            }
            if (!IsReady())
            {
                ProcessStartInfo server = new ProcessStartInfo
                {
                    FileName = python,
                    Arguments = "\"" + script + "\"",
                    WorkingDirectory = root,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                server.EnvironmentVariables["UAA_PORT"] = Port.ToString();
                Process.Start(server);
                for (int attempt = 0; attempt < 120 && !IsReady(); attempt++) Thread.Sleep(250);
            }
            if (!IsReady())
            {
                string detail = File.Exists(log) ? "\n\nLast launcher log:\n" + Tail(log, 1800) : "";
                ShowError("The dashboard did not start on port " + Port + "." + detail);
                return;
            }
            Process.Start(new ProcessStartInfo(DashboardUrl) { UseShellExecute = true });
        }
        catch (Exception error) { ShowError("The dashboard could not be started.\n\n" + error.Message); }
    }

    private static bool IsReady()
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(DashboardUrl + "/api/status");
            request.Timeout = 700;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                return response.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    private static string Tail(string path, int max)
    {
        try { string text = File.ReadAllText(path); return text.Length <= max ? text : text.Substring(text.Length - max); }
        catch { return "See " + path; }
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "Universal AutoApplier Dashboard", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
