"""One-shot build: python build_all.py "AULA F108PRO Reset.zip" [--kit]
extract stock image -> compile patch -> splice -> emulator tests -> patched flasher (-> private flash kit zip)
Options: --kit (also build the private flash kit zip), --config private/config_patch.json (bake saved keyboard settings).
Needs: pip install -r requirements-dev.txt, plus a clang with an ARM target (see firmware_patch/toolchain.py).
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
zip_path = sys.argv[1]
fp = os.path.join(ROOT, "firmware_patch")
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
flasher = os.path.join(ROOT, "dist", "AULA_F108Pro_patched_flasher.exe")


def run(*a):
    print("\n$", " ".join(os.path.relpath(x, ROOT) if os.path.isabs(x) else x for x in a), flush=True)
    subprocess.run([sys.executable, *a], check=True, cwd=ROOT)


run("tools/make_flasher.py", "extract", zip_path)
run("firmware_patch/build_patch.py")
cfg = ["--config", sys.argv[sys.argv.index("--config") + 1]] if "--config" in sys.argv else []
run("firmware_patch/build.py", os.path.join(fp, "stock_res4000.bin"), *cfg)
run("firmware_patch/test_emu.py", os.path.join(fp, "stock_res4000.bin"), os.path.join(fp, "patched.bin"))
run("tools/make_flasher.py", "build", zip_path, flasher)
if "--kit" in sys.argv:
    run("tools/make_kit.py", zip_path, flasher, os.path.join(ROOT, "dist", "aula-f108pro-flash-kit.zip"))
print("\nOK. Flasher:", flasher)
