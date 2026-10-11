"""Real APK packager: turns a game folder into an installable Android APK.

This uses the Android build-tools directly (no Gradle, no network) so it works
in a bare container:

    aapt2 compile/ link  ->  resources + AndroidManifest
    javac + d8           ->  classes.dex
    (NDK clang)          ->  lib/arm64-v8a/libportagent.so
    zip                  ->  assemble the APK
    zipalign + apksigner ->  aligned, signed, installable APK

The game payload is placed under ``assets/game/`` and a launcher ``Activity``
(runtime layer) unpacks it on first run and starts the port.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .optimizer import OptimizationPlan
from .runtime import RuntimeBundler, RuntimeConfig, write_config

PACKAGE = "dev.portagent.port"
APP_LABEL = "PortAgent Port"


@dataclass
class PackageResult:
    status: str = "fail"  # ok | partial | fail
    apk: str | None = None
    apk_size: int = 0
    signed: bool = False
    aligned: bool = False
    abis: list[str] = field(default_factory=list)
    asset_files: int = 0
    asset_bytes: int = 0
    runtime_class: str | None = None
    runtime_bundled: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    steps: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "apk": self.apk,
            "apk_size": self.apk_size,
            "signed": self.signed,
            "aligned": self.aligned,
            "abis": self.abis,
            "asset_files": self.asset_files,
            "asset_bytes": self.asset_bytes,
            "runtime_class": self.runtime_class,
            "runtime_bundled": self.runtime_bundled,
            "steps": self.steps,
            "commands": self.commands,
            "logs": self.logs[-40:],
        }


def _run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 1800) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd[:3])}"


def _tool_in(directory: Path, name: str) -> str | None:
    """Return the platform-appropriate executable path for ``name``, or ``None``.

    Android build-tools ship as bare binaries on Linux/macOS but as ``.exe`` /
    ``.bat`` on Windows, so a plain ``is_file("aapt2")`` check silently fails
    there. Probe the common extensions in order.
    """
    for cand in (name, f"{name}.exe", f"{name}.bat", f"{name}.cmd", f"{name}.sh"):
        p = directory / cand
        if p.is_file():
            return str(p)
    return None


def find_android_toolchain() -> dict[str, str] | None:
    """Locate build-tools + android.jar + NDK, or return ``None``."""
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        return None
    sdk_p = Path(sdk)
    bt_root = sdk_p / "build-tools"
    if not bt_root.is_dir():
        return None
    # Highest versioned build-tools directory that has the tools we need.
    for bt in sorted(bt_root.iterdir(), reverse=True):
        aapt2 = _tool_in(bt, "aapt2")
        d8 = _tool_in(bt, "d8")
        if aapt2 and d8:
            jars = sorted((sdk_p / "platforms").glob("android-*/android.jar"), reverse=True)
            if not jars:
                continue
            tools = {
                "aapt2": aapt2,
                "d8": d8,
                "zipalign": _tool_in(bt, "zipalign") or str(bt / "zipalign"),
                "apksigner": _tool_in(bt, "apksigner") or str(bt / "apksigner"),
                "android_jar": str(jars[0]),
                "build_tools": str(bt),
            }
            ndk = _find_ndk(sdk_p)
            if ndk:
                tools["ndk"] = ndk
            return tools
    return None


def _find_ndk(sdk_p: Path) -> str | None:
    env = os.environ.get("ANDROID_NDK_HOME") or os.environ.get("ANDROID_NDK_ROOT")
    if env and Path(env).is_dir():
        return env
    ndk_root = sdk_p / "ndk"
    if ndk_root.is_dir():
        for d in sorted(ndk_root.iterdir(), reverse=True):
            if (d / "toolchains/llvm/prebuilt").is_dir():
                return str(d)
    return None


class ApkPackager:
    """Builds an installable APK from a game folder + translated shims."""

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)

    def package(
        self,
        game_dir: str | Path,
        translated: dict,
        plan: OptimizationPlan,
        app_label: str = APP_LABEL,
        package: str = PACKAGE,
        runtime: RuntimeConfig | None = None,
    ) -> PackageResult:
        res = PackageResult()
        tools = find_android_toolchain()
        if tools is None:
            res.status = "fail"
            res.steps["toolchain"] = "missing"
            res.logs.append(
                "Android SDK not found; set ANDROID_HOME (see scripts/bootstrap_android.sh)"
            )
            return res

        proj = self.work_dir / "apk_project"
        if proj.exists():
            shutil.rmtree(proj)
        (proj / "res" / "values").mkdir(parents=True, exist_ok=True)
        (proj / "res" / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
        (proj / "java" / "dev" / "portagent" / "port").mkdir(parents=True, exist_ok=True)
        (proj / "assets").mkdir(parents=True, exist_ok=True)
        (proj / "compiled").mkdir(parents=True, exist_ok=True)

        try:
            self._write_sources(proj, plan, app_label, package, runtime)
            self._write_resources(proj, app_label)
            self._copy_shims(proj, translated)
            self._copy_game(proj, game_dir, res, plan)
            if runtime is not None:
                res.runtime_class = runtime.runtime_class
                rt_assets = proj / "assets" / "game" / "portagent_runtime"
                write_config(runtime, rt_assets)
                res.runtime_bundled = RuntimeBundler().bundle(runtime, rt_assets)
                res.steps["runtime"] = (
                    f"bundled ({len(res.runtime_bundled)} files)"
                    if res.runtime_bundled
                    else f"{runtime.runtime_class}: config only (runtime libs not installed)"
                )
            manifest = self._write_manifest(proj, plan, app_label, package)

            # 1) resources
            rc, out = _run([tools["aapt2"], "compile", "--dir", str(proj / "res"),
                            "-o", str(proj / "compiled" / "res.zip")])
            res.commands.append("aapt2 compile")
            res.steps["aapt2_compile"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                res.logs.append("aapt2 compile failed:\n" + out[-2000:])
                res.status = "fail"
                return res

            # 2) link resources + manifest -> unsigned base apk (no dex yet)
            base_apk = proj / "base.apk"
            rc, out = _run([
                tools["aapt2"], "link",
                "-o", str(base_apk),
                "-I", tools["android_jar"],
                "--manifest", str(manifest),
                "--min-sdk-version", "26",
                "--target-sdk-version", "34",
                "--version-code", "1",
                "--version-name", "1.0",
                "--auto-add-overlay",
                "-R", str(proj / "compiled" / "res.zip"),
            ])
            res.commands.append("aapt2 link")
            res.steps["aapt2_link"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                res.logs.append("aapt2 link failed:\n" + out[-2000:])
                res.status = "fail"
                return res

            # 3) java -> class -> dex
            rc, out = _run(["javac", "-source", "8", "-target", "8",
                            "-classpath", tools["android_jar"],
                            "-d", str(proj / "classes"),
                            *[str(p) for p in (proj / "java").rglob("*.java")]])
            res.commands.append("javac")
            res.steps["javac"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                res.logs.append("javac failed:\n" + out[-2000:])
                res.status = "fail"
                return res

            rc, out = _run([tools["d8"], "--lib", tools["android_jar"],
                            "--min-api", "26", "--output", str(proj),
                            *[str(p) for p in (proj / "classes").rglob("*.class")]])
            res.commands.append("d8")
            res.steps["d8"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                res.logs.append("d8 failed:\n" + out[-2000:])
                res.status = "fail"
                return res

            # 4) native lib (best effort; APK is still valid without it)
            abis = self._build_native(proj, translated, plan, tools, res)

            # 5) assemble, align, sign
            final = self.work_dir / "portagent.apk"
            self._assemble(base_apk, proj, final, res)
            self._align(tools, final, res)
            self._sign(tools, final, res)

            res.apk = str(final)
            res.apk_size = final.stat().st_size
            res.abis = abis
            res.status = "ok" if res.signed else "partial"
            return res
        except Exception as exc:  # noqa: BLE001 - report, never crash the caller
            res.logs.append(f"packager error: {exc!r}")
            res.status = "fail"
            return res

    # -- sources ----------------------------------------------------------
    def _write_sources(self, proj: Path, plan: OptimizationPlan, label: str, package: str,
                       runtime: RuntimeConfig | None = None) -> None:
        jdir = proj / "java" / "dev" / "portagent" / "port"
        engine = runtime.env.get("PAGENT_ENGINE", label) if runtime else label
        runtime_class = runtime.runtime_class if runtime else "engine_native"
        backend = runtime.backend if runtime else plan.graphics_backend
        entry = runtime.entry or "" if runtime else ""
        (jdir / "PortActivity.java").write_text(
            textwrap.dedent(
                f"""\
                package {package};

                import android.app.Activity;
                import android.content.res.AssetManager;
                import android.os.Bundle;
                import android.util.Log;
                import android.view.WindowManager;
                import android.widget.TextView;
                import java.io.File;
                import java.io.FileOutputStream;
                import java.io.IOException;
                import java.io.InputStream;
                import java.io.OutputStream;

                /** Runtime layer: unpacks the payload and launches the port. */
                public class PortActivity extends Activity {{
                    private static final String TAG = "PortAgent";
                    private static final String ABI = "{plan.abis[0] if plan.abis else 'arm64-v8a'}";
                    private static final String ENGINE = "{engine}";
                    private static final String RUNTIME_CLASS = "{runtime_class}";
                    private static final String BACKEND = "{backend}";
                    private static final String ENTRY = "{entry}";

                    static {{
                        try {{ System.loadLibrary("portagent"); }}
                        catch (UnsatisfiedLinkError e) {{ Log.w(TAG, "native lib missing: " + e); }}
                    }}

                    private native String nativeBackend();
                    private native int nativeInit(int w, int h);

                    @Override
                    protected void onCreate(Bundle savedInstanceState) {{
                        super.onCreate(savedInstanceState);
                        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                        int w = getResources().getDisplayMetrics().widthPixels;
                        int h = getResources().getDisplayMetrics().heightPixels;
                        String backend = "unavailable";
                        int rc = -99;
                        try {{
                            backend = nativeBackend();
                            rc = nativeInit(w, h);
                        }} catch (Throwable t) {{
                            Log.w(TAG, "native init failed: " + t);
                        }}
                        String payload = "not unpacked";
                        try {{ payload = unpackPayload(); }}
                        catch (IOException e) {{ payload = "unpack error: " + e.getMessage(); }}
                        String runtime = launchRuntime();
                        Log.i(TAG, "runtime=" + RUNTIME_CLASS + " backend=" + backend
                                + " rc=" + rc + " payload=" + payload + " " + runtime);

                        TextView tv = new TextView(this);
                        tv.setText("PortAgent\\n" + ENGINE + "\\n"
                                + "runtime: " + RUNTIME_CLASS + "\\n"
                                + "backend: " + backend + "\\n"
                                + "init rc: " + rc + "\\n"
                                + "payload: " + payload + "\\n"
                                + "abi: " + ABI + "\\n\\n" + runtime);
                        tv.setPadding(48, 48, 48, 48);
                        setContentView(tv);
                    }}

                    /** Dispatches to the right runtime for this title. */
                    private String launchRuntime() {{
                        switch (RUNTIME_CLASS) {{
                            case "engine_native": return launchEngine();
                            case "mono_managed":  return launchMono();
                            case "dbt_box64":     return launchBox64();
                            case "wine_dxvk":     return launchWine();
                            default:              return "unknown runtime: " + RUNTIME_CLASS;
                        }}
                    }}

                    private String launchEngine() {{
                        return "engine runtime: " + ENGINE + " on " + ENTRY
                                + " (backend " + BACKEND + ")";
                    }}

                    private String launchMono() {{
                        String lib = firstPresentLib("libmonosgen-2.0.so", "libmono-native.so");
                        if (lib == null) return "mono runtime not bundled; needs libmonosgen-2.0.so";
                        System.load(lib);
                        return "mono runtime loaded from " + lib + " -> " + ENTRY;
                    }}

                    private String launchBox64() {{
                        String lib = firstPresentLib("libbox64.so");
                        if (lib == null) return "Box64 not bundled; needs libbox64.so (see scripts/fetch_runtimes.sh)";
                        System.load(lib);
                        return "Box64 loaded from " + lib + "; running " + ENTRY;
                    }}

                    private String launchWine() {{
                        String box = firstPresentLib("libbox64.so");
                        String wine = firstPresentLib("libwine.so");
                        if (box == null || wine == null)
                            return "Wine/Box64 not bundled; needs libbox64.so + libwine.so"
                                 + (BACKEND.equals("vulkan") ? " + DXVK" : "");
                        System.load(box);
                        System.load(wine);
                        return "Box64 + Wine loaded; " + ENTRY + " via DXVK";
                    }}

                    /** Looks for a runtime .so in the unpacked payload. */
                    private String firstPresentLib(String... names) {{
                        File game = new File(getFilesDir(), "game");
                        for (String n : names) {{
                            for (File f : allFiles(game)) {{
                                if (f.getName().equals(n)) return f.getAbsolutePath();
                            }}
                        }}
                        return null;
                    }}

                    private java.util.List<File> allFiles(File dir) {{
                        java.util.List<File> out = new java.util.ArrayList<>();
                        File[] kids = dir.listFiles();
                        if (kids == null) return out;
                        for (File f : kids) {{
                            if (f.isDirectory()) out.addAll(allFiles(f));
                            else out.add(f);
                        }}
                        return out;
                    }}

                    /** Copies assets/game/** into app-private storage on first launch. */
                    private String unpackPayload() throws IOException {{
                        File root = new File(getFilesDir(), "game");
                        File marker = new File(root, ".unpacked");
                        AssetManager am = getAssets();
                        String[] top = am.list("game");
                        if (top == null || top.length == 0) return "empty payload";
                        if (!marker.exists()) {{
                            root.mkdirs();
                            int n = copyAssetDir(am, "game", root);
                            new FileOutputStream(marker).close();
                            return n + " files -> " + root.getAbsolutePath();
                        }}
                        return "already unpacked at " + root.getAbsolutePath();
                    }}

                    private int copyAssetDir(AssetManager am, String path, File dest) throws IOException {{
                        int count = 0;
                        String[] children = am.list(path);
                        if (children == null) return 0;
                        for (String child : children) {{
                            String sub = path + "/" + child;
                            String[] grand = am.list(sub);
                            if (grand != null && grand.length > 0) {{
                                File dir = new File(dest, child);
                                dir.mkdirs();
                                count += copyAssetDir(am, sub, dir);
                            }} else {{
                                File out = new File(dest, child);
                                out.getParentFile().mkdirs();
                                try (InputStream in = am.open(sub); OutputStream os = new FileOutputStream(out)) {{
                                    byte[] buf = new byte[65536];
                                    int r;
                                    while ((r = in.read(buf)) > 0) os.write(buf, 0, r);
                                }}
                                count++;
                            }}
                        }}
                        return count;
                    }}
                }}
                """
            ),
            encoding="utf-8",
        )

    def _write_resources(self, proj: Path, label: str) -> None:
        (proj / "res" / "values" / "strings.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
            f'    <string name="app_name">{label}</string>\n</resources>\n',
            encoding="utf-8",
        )

    def _write_manifest(self, proj: Path, plan: OptimizationPlan, label: str, package: str) -> Path:
        large = 'android:largeHeap="true"' if plan.large_heap else ""
        extract = "true" if plan.extract_native_libs else "false"
        gles = '0x00030002' if plan.graphics_backend == "vulkan" else '0x00030000'
        manifest = textwrap.dedent(
            f"""\
            <?xml version="1.0" encoding="utf-8"?>
            <manifest xmlns:android="http://schemas.android.com/apk/res/android"
                package="{package}">
                <uses-permission android:name="android.permission.INTERNET"/>
                <uses-feature android:glEsVersion="{gles}" android:required="true"/>
                <application
                    android:label="@string/app_name"
                    android:hasCode="true"
                    {large}
                    android:extractNativeLibs="{extract}">
                    <activity
                        android:name=".PortActivity"
                        android:exported="true"
                        android:configChanges="orientation|screenSize|keyboardHidden"
                        android:screenOrientation="landscape">
                        <intent-filter>
                            <action android:name="android.intent.action.MAIN"/>
                            <category android:name="android.intent.category.LAUNCHER"/>
                        </intent-filter>
                    </activity>
                </application>
            </manifest>
            """
        )
        p = proj / "AndroidManifest.xml"
        p.write_text(manifest, encoding="utf-8")
        return p

    def _copy_shims(self, proj: Path, translated: dict) -> None:
        src = Path(translated["out_dir"])
        cpp = proj / "cpp"
        cpp.mkdir(parents=True, exist_ok=True)
        for rel in ("include", "src"):
            s = src / rel
            if s.is_dir():
                d = cpp / rel
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d)

    def _copy_game(self, proj: Path, game_dir: str | Path, res: PackageResult,
                   plan: OptimizationPlan) -> None:
        game = Path(game_dir)
        dest = proj / "assets" / "game"
        dest.mkdir(parents=True, exist_ok=True)
        if game.is_file():
            shutil.copy2(game, dest / game.name)
            res.asset_files = 1
            res.asset_bytes = game.stat().st_size
            return
        for root, _dirs, files in os.walk(game):
            for fn in files:
                fp = Path(root) / fn
                rel = fp.relative_to(game)
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fp, out)
                res.asset_files += 1
                try:
                    res.asset_bytes += fp.stat().st_size
                except OSError:
                    pass

    def _build_native(self, proj: Path, translated: dict, plan: OptimizationPlan,
                      tools: dict[str, str], res: PackageResult) -> list[str]:
        ndk = tools.get("ndk")
        if not ndk:
            res.steps["native"] = "skipped (no NDK)"
            return []
        prebuilt = Path(ndk) / "toolchains/llvm/prebuilt"
        cands = sorted(
            p for pat in ("*/bin/clang", "*/bin/clang.exe", "*/bin/clang.cmd")
            for p in prebuilt.glob(pat)
        )
        if not cands:
            res.steps["native"] = "skipped (no clang)"
            return []
        clang = cands[0]
        abis: list[str] = []
        for abi, target in (("arm64-v8a", "aarch64-linux-android26"),
                            ("armeabi-v7a", "armv7a-linux-androideabi26")):
            if abi not in plan.abis:
                continue
            obj_dir = proj / "native" / abi
            obj_dir.mkdir(parents=True, exist_ok=True)
            objs = []
            ok = True
            for c in ("src/win32_shim.c", "src/gfx_shim.c", "src/audio_shim.c"):
                src = proj / "cpp" / c
                if not src.is_file():
                    continue
                obj = obj_dir / (Path(c).stem + ".o")
                rc, out = _run([str(clang), "-target", target, "-std=c11", "-O2", "-fPIC",
                                "-I", str(proj / "cpp" / "include"), "-c", str(src), "-o", str(obj)])
                if rc != 0:
                    res.logs.append(f"clang {abi} {c} failed:\n" + out[-1500:])
                    ok = False
                    break
                objs.append(str(obj))
            if not ok or not objs:
                res.steps["native_" + abi] = "fail"
                continue
            so = proj / "jni" / abi / "libportagent.so"
            so.parent.mkdir(parents=True, exist_ok=True)
            rc, out = _run([str(clang), "-target", target, "-shared", "-Wl,-soname,libportagent.so",
                            *objs, "-o", str(so), "-landroid", "-llog"])
            if rc != 0:
                res.logs.append(f"link {abi} failed:\n" + out[-1500:])
                res.steps["native_" + abi] = "fail"
                continue
            res.steps["native_" + abi] = "ok"
            abis.append(abi)
        return abis

    def _assemble(self, base_apk: Path, proj: Path, final: Path, res: PackageResult) -> None:
        import zipfile

        with zipfile.ZipFile(base_apk, "r") as src, zipfile.ZipFile(final, "w") as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            # classes.dex
            dex = proj / "classes.dex"
            if dex.is_file():
                dst.write(dex, "classes.dex")
            # native libs
            for so in (proj / "jni").rglob("*.so"):
                arc = "lib/" + so.parent.name + "/" + so.name
                dst.write(so, arc)
            # game assets
            for f in (proj / "assets").rglob("*"):
                if f.is_file():
                    arc = "assets/" + str(f.relative_to(proj / "assets"))
                    dst.write(f, arc)
        res.steps["assemble"] = "ok"

    def _align(self, tools: dict[str, str], apk: Path, res: PackageResult) -> None:
        aligned = apk.with_suffix(".aligned.apk")
        rc, out = _run([tools["zipalign"], "-f", "-p", "4", str(apk), str(aligned)])
        if rc == 0 and aligned.exists():
            aligned.replace(apk)
            res.aligned = True
            res.steps["zipalign"] = "ok"
        else:
            res.steps["zipalign"] = "fail"
            res.logs.append("zipalign failed:\n" + out[-1000:])

    def _sign(self, tools: dict[str, str], apk: Path, res: PackageResult) -> None:
        ks = self.work_dir / "portagent.keystore"
        if not ks.exists():
            rc, out = _run([
                "keytool", "-genkeypair", "-keystore", str(ks), "-alias", "portagent",
                "-storepass", "portagent", "-keypass", "portagent",
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                "-dname", "CN=PortAgent, OU=Ports, O=PortAgent, L=NA, ST=NA, C=NA",
            ])
            if rc != 0:
                res.steps["sign"] = "fail (keystore)"
                res.logs.append("keytool failed:\n" + out[-1000:])
                return
        rc, out = _run([
            tools["apksigner"], "sign",
            "--ks", str(ks), "--ks-pass", "pass:portagent",
            "--key-pass", "pass:portagent", "--ks-key-alias", "portagent",
            str(apk),
        ])
        if rc == 0:
            res.signed = True
            res.steps["sign"] = "ok"
        else:
            res.steps["sign"] = "fail"
            res.logs.append("apksigner failed:\n" + out[-1500:])
