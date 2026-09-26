"""Convert between DWG and DXF with AutoCAD's headless accoreconsole.

    python dwg_to_dxf.py "<path>\\Restaurant.dwg" [--out <dir>]          # DWG -> ASCII DXF, to read with ezdxf
    python dwg_to_dxf.py "<path>\\ELEV_MAIN.dxf" --to-dwg [--out <dir>]  # DXF -> DWG (AUDIT first), for Revit

- DWG -> DXF: the source is copied first (it may be open and locked in AutoCAD), then DXFOUT runs on the copy.
- DXF -> DWG: some ezdxf-written DXFs are refused by Revit's importer (Document.Import returns False, no message).
  Opening them in AutoCAD, AUDIT (fix) and SAVEAS DWG gives a file Revit imports.
Writes <out>\\<name>.dxf / .dwg (default: the current directory). Needs AutoCAD (any recent version) installed.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile


def find_accoreconsole(hint=None):
    cands = []
    if hint:
        cands.append(os.path.join(hint, "accoreconsole.exe"))
    cands += sorted(glob.glob(r"C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe"), reverse=True)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def run_script(exe, src, script, out_file):
    work = os.path.dirname(src)
    scr = os.path.join(work, "run.scr")
    with open(scr, "w") as f:
        f.write(script)
    proc = subprocess.run([exe, "/i", src, "/s", scr, "/l", "en-US"], capture_output=True, timeout=900)
    if not os.path.isfile(out_file):
        print(proc.stdout.decode("utf-16-le", "ignore")[-3000:])
        sys.exit("accoreconsole failed (exit {})".format(proc.returncode))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=".")
    ap.add_argument("--to-dwg", action="store_true", help="DXF -> DWG (audited) instead of DWG -> DXF")
    ap.add_argument("--acad", default=None, help="AutoCAD install folder (default: newest found)")
    a = ap.parse_args()

    exe = find_accoreconsole(a.acad)
    if not exe:
        sys.exit("accoreconsole.exe not found - install AutoCAD or pass --acad")
    name = os.path.splitext(os.path.basename(a.src))[0]
    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="acadconv_")      # no spaces: the script's paths stay unquoted
    try:
        if a.to_dwg:
            src = os.path.join(work, "src.dxf")
            shutil.copy2(a.src, src)
            tmp = os.path.join(work, "out.dwg")
            run_script(exe, src, "_.FILEDIA 0\n_.AUDIT _Y\n_.SAVEAS 2018 {}\n_.QUIT _Y\n".format(tmp.replace("\\", "/")), tmp)
            dst = os.path.join(out_dir, name + ".dwg")
        else:
            src = os.path.join(work, "src.dwg")
            shutil.copy2(a.src, src)
            tmp = os.path.join(work, "out.dxf")
            # FILEDIA 0 keeps DXFOUT on the command line; 16 = decimal places
            run_script(exe, src, "_.FILEDIA 0\n_.DXFOUT\n{}\n16\n_.QUIT _Y\n".format(tmp.replace("\\", "/")), tmp)
            dst = os.path.join(out_dir, name + ".dxf")
        shutil.move(tmp, dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("{}: {} ({:.1f} MB) via {}".format("DWG" if a.to_dwg else "DXF", dst, os.path.getsize(dst) / 1e6, exe))


if __name__ == "__main__":
    main()
