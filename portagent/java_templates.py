"""Java sources for the generated container launcher (plain strings).

Split out from ``packager.py`` so the Java stays readable: no Python brace
escaping, and it can be linted/eyeballed as Java.  ``packager._write_container_activity``
substitutes the ``__TOKEN__`` placeholders at build time.

The three classes together replace the old single blocking Activity:

``GpuProbe``     queries the real GL renderer string so the GPU vendor is known.
``Device``       detects RAM/SoC/panel and derives render size, fps cap, heap.
``PortService``  runs Box64+Wine off the UI thread in a foreground service.
``PortActivity`` shows status and starts the service.

Everything the app cannot do (e.g. no X server in the bundle) is reported by
name on screen instead of a grey/black frame.
"""

# Query the GPU vendor without a window: a 1x1 pbuffer EGL context is enough to
# read GL_RENDERER, which is the only reliable vendor signal on Android.
GPU_PROBE_JAVA = r"""package __PACKAGE__;

/** Minimal headless GL probe: reads the renderer string to learn the GPU vendor. */
public final class GpuProbe {
    public static String renderer() {
        android.opengl.EGLDisplay dpy = android.opengl.EGL14.eglGetDisplay(
                android.opengl.EGL14.EGL_DEFAULT_DISPLAY);
        if (dpy == android.opengl.EGL14.EGL_NO_DISPLAY) return "";
        int[] ver = new int[2];
        if (!android.opengl.EGL14.eglInitialize(dpy, ver, 0, ver, 1)) return "";
        int[] cfgAttr = {
                android.opengl.EGL14.EGL_RENDERABLE_TYPE,
                android.opengl.EGL14.EGL_OPENGL_ES2_BIT,
                android.opengl.EGL14.EGL_SURFACE_TYPE,
                android.opengl.EGL14.EGL_PBUFFER_BIT,
                android.opengl.EGL14.EGL_RED_SIZE, 8,
                android.opengl.EGL14.EGL_GREEN_SIZE, 8,
                android.opengl.EGL14.EGL_BLUE_SIZE, 8,
                android.opengl.EGL14.EGL_NONE};
        android.opengl.EGLConfig[] cfgs = new android.opengl.EGLConfig[1];
        int[] num = new int[1];
        if (!android.opengl.EGL14.eglChooseConfig(dpy, cfgAttr, 0, cfgs, 0, 1, num, 0)
                || num[0] == 0) {
            android.opengl.EGL14.eglTerminate(dpy);
            return "";
        }
        int[] ctxAttr = {android.opengl.EGL14.EGL_CONTEXT_CLIENT_VERSION, 2,
                android.opengl.EGL14.EGL_NONE};
        android.opengl.EGLContext ctx = android.opengl.EGL14.eglCreateContext(
                dpy, cfgs[0], android.opengl.EGL14.EGL_NO_CONTEXT, ctxAttr, 0);
        int[] pbAttr = {android.opengl.EGL14.EGL_WIDTH, 1,
                android.opengl.EGL14.EGL_HEIGHT, 1, android.opengl.EGL14.EGL_NONE};
        android.opengl.EGLSurface surf = android.opengl.EGL14.eglCreatePbufferSurface(
                dpy, cfgs[0], pbAttr, 0);
        String r = "";
        if (android.opengl.EGL14.eglMakeCurrent(dpy, surf, surf, ctx)) {
            String s = android.opengl.GLES20.glGetString(android.opengl.GLES20.GL_RENDERER);
            if (s != null) r = s;
        }
        android.opengl.EGL14.eglMakeCurrent(dpy, android.opengl.EGL14.EGL_NO_SURFACE,
                android.opengl.EGL14.EGL_NO_SURFACE, android.opengl.EGL14.EGL_NO_CONTEXT);
        android.opengl.EGL14.eglTerminate(dpy);
        return r;
    }

    public static String classify(String renderer, String soc) {
        String b = (renderer + " " + soc).toLowerCase();
        if (b.contains("mali")) return "mali";
        if (b.contains("adreno")) return "adreno";
        if (b.contains("powervr") || b.contains("power vr")) return "powervr";
        if (b.contains("xclipse")) return "xclipse";
        return "unknown";
    }
}
"""

DEVICE_JAVA = r"""package __PACKAGE__;

import android.app.ActivityManager;
import android.content.Context;
import android.os.Build;
import android.util.DisplayMetrics;

/**
 * Per-device tuning.  A 1080x2460 panel is 2.6x the pixels of 720x1600; rendering
 * it natively on a mid SoC thermal-throttles the phone.  We pick a smaller
 * render target and let the GPU upscale, and we cap the frame rate so an
 * uncapped DXVK loop cannot peg every core.
 */
public final class Device {
    public final String model;
    public final String soc;
    public final String renderer;
    public final String gpu;       // mali | adreno | powervr | xclipse | unknown
    public final int ramMb;
    public final int tier;         // 0 low, 1 mid, 2 high
    public final int panelW;
    public final int panelH;
    public final int renderW;
    public final int renderH;
    public final int fpsCap;
    public final int heapMb;
    public final String driver;    // turnip | virgl | system

    private Device(String model, String soc, String renderer, String gpu, int ramMb,
                   int panelW, int panelH, int renderW, int renderH, int fpsCap,
                   int heapMb, String driver) {
        this.model = model; this.soc = soc; this.renderer = renderer; this.gpu = gpu;
        this.ramMb = ramMb; this.panelW = panelW; this.panelH = panelH;
        this.renderW = renderW; this.renderH = renderH; this.fpsCap = fpsCap;
        this.heapMb = heapMb; this.driver = driver;
        this.tier = ramMb <= 0 ? 1 : (ramMb < 4000 ? 0 : (ramMb < 7000 ? 1 : 2));
    }

    public static Device detect(Context ctx) {
        DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
        int pw = dm.widthPixels, ph = dm.heightPixels;
        int ram = totalRamMb(ctx);
        String model = nz(Build.MODEL);
        String soc = nz(Build.HARDWARE);
        if (Build.VERSION.SDK_INT >= 31) {
            try { soc = nz(firstNonEmpty(Build.SOC_MODEL, soc)); } catch (Throwable ignored) {}
        }
        String renderer = "";
        try { renderer = nz(GpuProbe.renderer()); } catch (Throwable ignored) {}
        String gpu = GpuProbe.classify(renderer, soc + " " + model);
        int tier = ram <= 0 ? 1 : (ram < 4000 ? 0 : (ram < 7000 ? 1 : 2));

        int[] longEdge = {720, 960, 1440};
        int[] caps = {30, 40, 60};
        int[] heaps = {512, 1024, 2048};
        int shortEdge = Math.min(pw, ph), longP = Math.max(pw, ph);
        int target = longEdge[tier];
        double scale = longP > 0 ? Math.min(1.0, (double) target / longP) : 1.0;
        int rw = (int) Math.round(shortEdge * scale);
        int rh = (int) Math.round(longP * scale);
        rw -= rw % 2; rh -= rh % 2;
        if (rw < 2 || rh < 2) { rw = 640; rh = 360; }

        String driver;
        if ("adreno".equals(gpu) || "xclipse".equals(gpu)) driver = "turnip";
        else if ("mali".equals(gpu) || "powervr".equals(gpu)) driver = "virgl";
        else driver = "system";

        return new Device(model, soc, renderer, gpu, ram, pw, ph, rw, rh, caps[tier],
                heaps[tier], driver);
    }

    private static int totalRamMb(Context ctx) {
        try {
            ActivityManager am = (ActivityManager)
                    ctx.getSystemService(Context.ACTIVITY_SERVICE);
            ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
            am.getMemoryInfo(mi);
            return (int) (mi.totalMem / (1024 * 1024));
        } catch (Throwable t) {
            return 0;
        }
    }

    private static String firstNonEmpty(String... xs) {
        for (String x : xs) if (x != null && x.length() > 0) return x;
        return "";
    }

    private static String nz(String s) { return s == null ? "" : s; }

    public String summary() {
        return model + " (" + soc + ")\n"
                + "gpu: " + gpu + (renderer.isEmpty() ? "" : " [" + renderer + "]") + "\n"
                + "ram: " + ramMb + " MB, tier " + tier + "\n"
                + "panel: " + panelW + "x" + panelH + " -> render " + renderW + "x" + renderH
                + " (" + Math.round(scale() * 100) + "%)\n"
                + "fps cap: " + fpsCap + ", heap: " + heapMb + " MB, driver: " + driver;
    }

    public double scale() {
        int longP = Math.max(panelW, panelH);
        return longP > 0 ? (double) Math.max(renderW, renderH) / longP : 1.0;
    }
}
"""

SERVICE_JAVA = r"""package __PACKAGE__;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.res.AssetManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * Runs the port off the UI thread in a foreground service.
 *
 * The previous design ran a blocking waitFor() inside onCreate(), which froze the
 * launcher Activity and let the system throttle/keep the child out of the
 * foreground.  Here the heavy work runs on its own thread with a foreground
 * notification and a partial wake lock, so the game process survives and the
 * phone stays responsive.
 */
public class PortService extends Service {
    public static final String TAG = "PortAgent";
    public static final String ACTION_START = "__PACKAGE__.START";
    private static final String CHANNEL = "portagent";
    private static final int NOTIF_ID = 1;

    // Shared status the Activity polls (no Binder plumbing needed).
    public static volatile String status = "idle";
    private static final StringBuilder LOG = new StringBuilder();
    private static final Object LOCK = new Object();

    private Thread worker;
    private Process child;
    private PowerManager.WakeLock wake;

    public static String log() {
        synchronized (LOCK) { return LOG.toString(); }
    }

    private static void say(String line) {
        synchronized (LOCK) {
            LOG.append(line).append('\n');
            if (LOG.length() > 20000) LOG.delete(0, LOG.length() - 20000);
        }
        status = line;
        Log.i(TAG, line);
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(NOTIF_ID, notification());
        if (worker == null || !worker.isAlive()) {
            worker = new Thread(new Runnable() { public void run() { boot(); } }, "portagent-boot");
            worker.start();
        }
        return START_STICKY;
    }

    private Notification notification() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm.getNotificationChannel(CHANNEL) == null) {
                nm.createNotificationChannel(new NotificationChannel(
                        CHANNEL, "PortAgent", NotificationManager.IMPORTANCE_LOW));
            }
        }
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setContentTitle("PortAgent")
                .setContentText("Running __ENGINE__")
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setOngoing(true).build();
    }

    private void acquireWake() {
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "portagent:run");
            wake.acquire();
        } catch (Throwable ignored) {}
    }

    private void boot() {
        acquireWake();
        try {
            Device dev = Device.detect(this);
            say("device: " + dev.model + " | gpu " + dev.gpu + " | ram " + dev.ramMb
                    + " MB | tier " + dev.tier);
            say("render " + dev.renderW + "x" + dev.renderH + " | fps cap " + dev.fpsCap
                    + " | heap " + dev.heapMb + " MB | driver " + dev.driver);

            File runtime = new File(getFilesDir(), "runtime");
            File payload = new File(getFilesDir(), "payload");
            say(unpack("game", payload));
            say(unpack("runtime", runtime));
            say(prepareWine(runtime, dev));

            // Wine's only display driver here is X11; without an X server it
            // cannot create a window, which is exactly the grey/black screen.
            File xserver = findXServer(runtime);
            if (xserver == null) {
                say("DISPLAY: no X server in the bundle yet -> Wine cannot open a "
                        + "window. Rendering is not started. (Needs the Xlorie/Xvfb "
                        + "component at assets/runtime/display/.)");
            } else {
                say("display server: " + xserver.getAbsolutePath());
            }

            launch(runtime, payload, dev, xserver);
        } catch (Throwable t) {
            say("FAILED: " + t);
            Log.e(TAG, "boot failed", t);
        }
    }

    private void launch(File runtime, File payload, Device dev, File xserver)
            throws IOException, InterruptedException {
        File box64 = new File(runtime, "box64/usr/local/bin/box64");
        File loader = new File(runtime, "rootfs/usr/lib/ld-linux-aarch64.so.1");
        File wine = new File(runtime, "rootfs/opt/wine/bin/wine");
        if (!box64.isFile()) { say("box64 missing at " + box64); return; }
        if (!wine.isFile()) { say("wine missing at " + wine); return; }
        File exe = findExe(payload);
        if (exe == null) { say("no .exe found in payload"); return; }

        box64.setExecutable(true);
        wine.setExecutable(true);
        File prefix = new File(getFilesDir(), "prefix");
        prefix.mkdirs();

        List<String> cmd = new ArrayList<>();
        cmd.add(loader.getAbsolutePath());       // box64's PT_INTERP is Winlator's path
        cmd.add(box64.getAbsolutePath());
        cmd.add(wine.getAbsolutePath());
        cmd.add(exe.getAbsolutePath());

        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.directory(prefix);
        pb.redirectErrorStream(true);
        String rootLib = new File(runtime, "rootfs/usr/lib").getAbsolutePath();
        String x64Lib = new File(runtime, "rootfs/usr/lib/x86_64-linux-gnu").getAbsolutePath();

        // Device-aware tuning: this is what keeps the *phone* responsive.
        int hz = Math.max(60, dev.fpsCap);
        pb.environment().put("PAGENT_FPS_CAP", Integer.toString(dev.fpsCap));
        pb.environment().put("DXVK_FRAME_RATE", Integer.toString(dev.fpsCap));
        pb.environment().put("MESA_VK_WSI_PRESENT_MODE", "fifo");     // vsync instead of spin
        pb.environment().put("vblank_mode", "1");
        pb.environment().put("PAGENT_RENDER_W", Integer.toString(dev.renderW));
        pb.environment().put("PAGENT_RENDER_H", Integer.toString(dev.renderH));
        pb.environment().put("PAGENT_GPU", dev.gpu);
        // Box64 dynarec strength by tier: a weak phone in "fast" mode stalls.
        if (dev.tier == 0) {
            pb.environment().put("BOX64_DYNAREC_BIGBLOCK", "0");
            pb.environment().put("BOX64_DYNAREC_SAFEFLAGS", "2");
            pb.environment().put("BOX64_DYNAREC_STRONGMEM", "1");
        } else if (dev.tier == 1) {
            pb.environment().put("BOX64_DYNAREC_BIGBLOCK", "2");
            pb.environment().put("BOX64_DYNAREC_SAFEFLAGS", "1");
            pb.environment().put("BOX64_DYNAREC_STRONGMEM", "1");
        } else {
            pb.environment().put("BOX64_DYNAREC_BIGBLOCK", "3");
            pb.environment().put("BOX64_DYNAREC_SAFEFLAGS", "0");
            pb.environment().put("BOX64_DYNAREC_STRONGMEM", "0");
        }
        pb.environment().put("BOX64_DYNAREC_CALLRET", "1");
        pb.environment().put("BOX64_DYNAREC", "1");
        pb.environment().put("BOX64_LOG", "0");
        pb.environment().put("BOX64_NOBANNER", "1");
        pb.environment().put("BOX64_MAX_CPU", Integer.toString(Math.max(2, Math.min(4, dev.ramMb / 2048))));
        pb.environment().put("WINEPREFIX", prefix.getAbsolutePath());
        pb.environment().put("LD_LIBRARY_PATH", rootLib + ":" + x64Lib);
        pb.environment().put("BOX64_LD_LIBRARY_PATH", rootLib + ":" + x64Lib);
        pb.environment().put("WINEDLLPATH", new File(runtime, "rootfs/opt/wine/lib/wine").getAbsolutePath());
        pb.environment().put("WINELOADERNOEXEC", "1");
        pb.environment().put("WINEDEBUG", "-all");
        pb.environment().put("WINEDLLOVERRIDES", "d3d8,d3d9,d3d10core,d3d11,d3d12,d3d12core,dxgi=n,b");
        pb.environment().put("HOME", getFilesDir().getAbsolutePath());
        if (xserver != null) pb.environment().put("DISPLAY", ":0");

        // Only point at a driver that can actually drive this GPU.
        File icd = new File(runtime,
                "drivers/" + dev.driver + "/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json");
        if (dev.driver.equals("turnip") && icd.isFile()) {
            pb.environment().put("VK_ICD_FILENAMES", icd.getAbsolutePath());
        } else if (dev.driver.equals("system")) {
            say("GPU driver: system Vulkan (no bundled driver for " + dev.gpu + ")");
        }

        Process p = pb.start();
        child = p;
        say("launched: " + exe.getName() + " pid=" + pidOf(p));
        try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            String line; int n = 0;
            while ((line = r.readLine()) != null && n++ < 200) say("  " + line);
        }
        int code = p.waitFor();
        say("game exited: " + code);
    }

    private long pidOf(Process p) {
        try {
            java.lang.reflect.Field f = p.getClass().getDeclaredField("pid");
            f.setAccessible(true);
            return f.getInt(p);
        } catch (Throwable t) { return -1; }
    }

    /** Looks for an X server binary the bundle may ship (Xlorie/Xvfb/Xorg/Xwayland). */
    private File findXServer(File runtime) {
        return findNamed(runtime, new String[]{"Xlorie", "Xvfb", "Xorg", "Xwayland", "xserver"});
    }

    private File findNamed(File dir, String[] names) {
        File[] kids = dir.listFiles();
        if (kids == null) return null;
        for (File f : kids) {
            if (f.isDirectory()) {
                File r = findNamed(f, names);
                if (r != null) return r;
            } else {
                for (String n : names) {
                    if (f.getName().equals(n)) return f;
                }
            }
        }
        return null;
    }

    private File findExe(File dir) {
        File[] kids = dir.listFiles();
        if (kids == null) return null;
        File best = null;
        for (File f : kids) {
            if (f.isDirectory()) {
                File sub = findExe(f);
                if (sub != null && (best == null || sub.length() > best.length())) best = sub;
            } else if (f.getName().toLowerCase().endsWith(".exe")) {
                if (best == null || f.length() > best.length()) best = f;
            }
        }
        return best;
    }

    private String prepareWine(File runtime, Device dev) throws IOException {
        File prefix = new File(getFilesDir(), "prefix");
        File sys32 = new File(prefix, "drive_c/windows/system32");
        File syswow = new File(prefix, "drive_c/windows/syswow64");
        sys32.mkdirs(); syswow.mkdirs();
        int n = installDlls(new File(runtime, "dxvk"), sys32, syswow)
              + installDlls(new File(runtime, "vkd3d"), sys32, syswow);
        return "wine prefix ready (" + n + " translation DLLs)";
    }

    private int installDlls(File src, File sys32, File syswow) throws IOException {
        return copyDllDir(new File(src, "system32"), sys32)
             + copyDllDir(new File(src, "syswow64"), syswow);
    }

    private int copyDllDir(File from, File to) throws IOException {
        File[] kids = from.listFiles();
        if (kids == null) return 0;
        int n = 0;
        for (File f : kids) {
            if (f.isFile() && f.getName().toLowerCase().endsWith(".dll")) {
                copyFile(f, new File(to, f.getName())); n++;
            }
        }
        return n;
    }

    private String unpack(String assetPath, File dest) throws IOException {
        AssetManager am = getAssets();
        String[] top = am.list(assetPath);
        if (top == null || top.length == 0) return "assets/" + assetPath + ": empty";
        dest.mkdirs();
        return "assets/" + assetPath + ": " + copyDir(am, assetPath, dest) + " files";
    }

    private int copyDir(AssetManager am, String path, File dest) throws IOException {
        int count = 0;
        String[] children = am.list(path);
        if (children == null) return 0;
        for (String child : children) {
            String sub = path + "/" + child;
            String[] grand = am.list(sub);
            if (grand != null && grand.length > 0) {
                File dir = new File(dest, child); dir.mkdirs();
                count += copyDir(am, sub, dir);
            } else {
                File out = new File(dest, child);
                out.getParentFile().mkdirs();
                try (InputStream in = am.open(sub); OutputStream os = new FileOutputStream(out)) {
                    byte[] buf = new byte[1 << 16]; int r;
                    while ((r = in.read(buf)) > 0) os.write(buf, 0, r);
                }
                count++;
            }
        }
        return count;
    }

    private void copyFile(File from, File to) throws IOException {
        try (InputStream in = new FileInputStream(from); OutputStream os = new FileOutputStream(to)) {
            byte[] buf = new byte[1 << 16]; int r;
            while ((r = in.read(buf)) > 0) os.write(buf, 0, r);
        }
    }

    @Override
    public void onDestroy() {
        if (child != null) child.destroy();
        if (wake != null && wake.isHeld()) wake.release();
        super.onDestroy();
    }
}
"""

ACTIVITY_JAVA = r"""package __PACKAGE__;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.TextView;

/**
 * Thin UI: shows what the service is doing and keeps the screen on.  The real
 * work happens in PortService so the UI thread never blocks.
 */
public class PortActivity extends Activity {
    private TextView tv;
    private final Handler h = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        tv = new TextView(this);
        tv.setPadding(48, 48, 48, 48);
        setContentView(tv);

        try {
            startForegroundServiceCompat(new Intent(this, PortService.class)
                    .setAction(PortService.ACTION_START));
        } catch (Throwable t) {
            tv.setText("PortAgent\nfailed to start service: " + t);
            return;
        }
        h.post(poll);
    }

    private void startForegroundServiceCompat(Intent i) {
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
        else startService(i);
    }

    private final Runnable poll = new Runnable() {
        public void run() {
            tv.setText("PortAgent  __ENGINE__  [__ABI__ / __BACKEND__]\n\n" + PortService.log());
            h.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onDestroy() {
        h.removeCallbacks(poll);
        super.onDestroy();
    }
}
"""
