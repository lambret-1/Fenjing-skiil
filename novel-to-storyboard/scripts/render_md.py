#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_md.py —— 把章级 JSON 渲染成一份 Markdown 分镜文档

文档结构固定：
    # seedance2.0提示词
    共X段，X个镜头，总时长：X秒
    ---
    所需场景：……
    所需人物：……
    ## 第1段，X个子镜头
    ```text
    （一条 Clip 的完整提示词）
    ```

用法：
    python3 render_md.py chapter.json -o 分镜.md
    python3 render_md.py chapter.json --clip 1 -o 第1段.md
    python3 render_md.py chapter.json --check
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from render_sd2 import load, render_clip, check_chapter  # noqa: E402


def fmt_num(x):
    x = float(x)
    return int(x) if x.is_integer() else x


def render_md(chapter, clips):
    chars = {c["id"]: c for c in chapter["characters"]}
    scenes = {s["id"]: s for s in chapter["scenes"]}
    total_shots = sum(len(c["shots"]) for c in clips)
    total_s = sum(float(c["duration_s"]) for c in clips)

    scene_names = []
    for c in clips:
        sc = scenes.get(c.get("scene"))
        if sc and sc["name"] not in scene_names:
            scene_names.append(sc["name"])

    cast_names = []
    for c in clips:
        for cid in c.get("cast", []):
            if cid in chars and chars[cid]["name"] not in cast_names:
                cast_names.append(chars[cid]["name"])

    lines = ["# seedance2.0提示词", ""]
    lines.append("共{}段，{}个镜头，总时长：{}秒".format(len(clips), total_shots, fmt_num(total_s)))
    lines += ["", "---", ""]
    lines.append("所需场景：" + ("、".join(scene_names) if scene_names else "无"))
    lines.append("所需人物：" + ("、".join(cast_names) if cast_names else "无"))

    for c in clips:
        lines += ["", "## 第{}段，{}个子镜头".format(c["no"], len(c["shots"])), "", "```text"]
        lines.append(render_clip(chapter, c).rstrip("\n"))
        lines.append("```")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="小说转分镜 · Markdown 文档渲染")
    ap.add_argument("json_path", help="章级 JSON")
    ap.add_argument("--clip", type=int, help="只出第 N 段")
    ap.add_argument("--check", action="store_true", help="只校验，不出文档")
    ap.add_argument("-o", "--out", help="输出 .md 文件；不填则打印到终端")
    args = ap.parse_args()

    chapter = load(args.json_path)
    clips = chapter.get("clips", [])

    errs, warns = check_chapter(chapter)
    for e in errs:
        print("[硬伤]", e, file=sys.stderr)
    for w in warns:
        print("[提醒]", w, file=sys.stderr)

    if errs:
        print("\n有 {} 处硬伤，先修 JSON 再出文档".format(len(errs)), file=sys.stderr)
        sys.exit(1)
    if args.check:
        print("校验完成：0 处硬伤，{} 处提醒".format(len(warns)))
        return

    if args.clip:
        targets = [c for c in clips if c["no"] == args.clip]
        if not targets:
            print("找不到第 {} 段".format(args.clip), file=sys.stderr)
            sys.exit(1)
    else:
        targets = clips

    text = render_md(chapter, targets)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("已写入 {}（{} 字）".format(args.out, len(text)))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
