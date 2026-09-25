#!/usr/bin/env python3
"""
Build preview.html — a read-only copy of index.html with all data/*.json inlined,
so it can be opened straight from disk (no GitHub, no server) to review the import.

  python tools/make_preview.py            -> writes preview.html next to index.html
"""
import json, os, glob
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

payload = {}
for p in glob.glob(os.path.join(DATA, "**", "*.json"), recursive=True):
    rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
    with open(p) as f:
        payload[rel] = json.load(f)

html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
selftest = "--selftest" in __import__("sys").argv
inject = "<script>window.CCGL_PREVIEW_DATA=" + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") + ";" + ("window.CCGL_SELFTEST=true;" if selftest else "") + "</script>\n<script>"
out = html.replace("<script>\n/* ====", inject + "\n/* ====", 1)
assert out != html, "could not find script anchor in index.html"
open(os.path.join(ROOT, "preview.html"), "w", encoding="utf-8").write(out)
print(f"preview.html written — {len(out)//1024} KB, {len(payload)} data files inlined")
