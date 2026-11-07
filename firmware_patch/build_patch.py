"""Compile patch.c -> patch.bin + patch.elf (Cortex-M0, linked at 0x1A000). usage: python build_patch.py"""
import os, subprocess

import toolchain

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*cmd):
    subprocess.run(cmd, cwd=HERE, check=True)


run(toolchain.tool("clang"), "-target", "thumbv6m-none-eabi", "-mcpu=cortex-m0plus", "-mthumb", "-Os",
    "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables", "-Wall", "-c", "patch.c", "-o", "patch.o")
run(toolchain.tool("ld.lld"), "-T", "link.ld", "--entry=cmd_hook", "patch.o", "-o", "patch.elf")
run(toolchain.tool("llvm-objcopy"), "-O", "binary", "patch.elf", "patch.bin")
print("patch.bin:", os.path.getsize(os.path.join(HERE, "patch.bin")), "bytes")
