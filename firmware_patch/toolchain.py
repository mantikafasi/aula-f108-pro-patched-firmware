"""Locate an LLVM toolchain that can target Cortex-M0 (thumbv6m). Any of these work:
  - set LLVM_BIN to a bin/ folder of an LLVM build with the ARM target,
  - Android NDK (r23+) under %LOCALAPPDATA%\\Android\\Sdk\\ndk,
  - clang/ld.lld/llvm-objcopy/llvm-nm on PATH (the clang must list an 'arm' target).
"""
import glob, os, shutil, subprocess

EXE = ".exe" if os.name == "nt" else ""


def _candidates():
    if os.environ.get("LLVM_BIN"):
        yield os.environ["LLVM_BIN"]
    on_path = shutil.which("clang")
    if on_path:
        yield os.path.dirname(on_path)
    home = os.path.expanduser("~")
    for pat in (home + "/AppData/Local/Android/Sdk/ndk/*/toolchains/llvm/prebuilt/*/bin",
                home + "/Android/Sdk/ndk/*/toolchains/llvm/prebuilt/*/bin",
                "/usr/lib/llvm-*/bin", "/opt/homebrew/opt/llvm/bin"):
        yield from sorted(glob.glob(pat), reverse=True)


def bin_dir():
    for d in _candidates():
        clang = os.path.join(d, "clang" + EXE)
        if not os.path.exists(clang):
            continue
        try:
            targets = subprocess.check_output([clang, "--print-targets"], text=True, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        if " arm " in targets or "thumb" in targets:
            return d
    raise SystemExit("No clang with an ARM target found. Install the Android NDK or LLVM, or set LLVM_BIN.")


def tool(name):
    p = os.path.join(bin_dir(), name + EXE)
    if not os.path.exists(p):
        raise SystemExit(f"{name} not found next to clang in {bin_dir()}")
    return p
