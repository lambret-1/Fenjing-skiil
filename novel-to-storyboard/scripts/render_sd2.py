#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_sd2.py —— 小说转分镜 · Seedance 2.0 章级渲染与校验

输入一份章级 JSON，输出可直接粘进豆包/即梦的围栏提示词。
一条 Clip 一份产物：@图片N = 名称 → 【人物与场景】 → 【镜头N】 → <环境音> → （配乐） → 【约束】

用法：
    python3 render_sd2.py chapter.json --list                 # 列出所有 Clip
    python3 render_sd2.py chapter.json --clip 1               # 渲染第 1 条
    python3 render_sd2.py chapter.json --clip 1 --check       # 只校验第 1 条
    python3 render_sd2.py chapter.json --check                # 校验全章
    python3 render_sd2.py chapter.json --all                  # 渲染全章
    python3 render_sd2.py chapter.json --clip 1 --fence       # 套 ```text 围栏
    python3 render_sd2.py chapter.json --clip 1 -o clip01.txt # 写入文件
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "..", "assets", "schema.json")

MOVE_KEYS = ["推", "拉", "摇", "移", "升", "降", "环绕", "甩", "跟", "变焦"]

STAB_PHRASE = {
    "稳定": None,
    "三脚架稳定": None,
    "云台稳定": None,
    "呼吸感微动": "轻微呼吸感",
    "步伐微晃": "步伐带轻微晃动",
    "微晃": "手持微晃",
    "手持晃动": "手持晃动",
    "剧烈晃动": "剧烈晃动",
}

LANG_NAME = {"ja": "日语", "en": "英语", "ko": "韩语", "fr": "法语", "es": "西班牙语"}

# 画面五要素检查用
PLACE_WORDS = [
    "画面", "前景", "中景", "背景", "远处", "近处", "左侧", "右侧", "左", "右",
    "中央", "居中", "偏左", "偏右", "上方", "下方", "对角", "三分之一", "尽头",
]
LIGHT_WORDS = [
    "光", "影", "亮", "暗", "逆光", "侧光", "散射", "天光", "灯", "烛", "月色",
    "日光", "晨", "暮", "昏", "反光", "发灰", "投影",
]
ENV_WORDS = [
    "雾", "雨", "雪", "尘", "风", "水", "河", "泥", "岸", "街", "屋", "林",
    "草", "烟", "人群", "空", "背景", "前景", "空气", "气味", "灰",
]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def join_text(parts):
    """把若干句合成一段：前句以句末标点结尾就直接接，否则用逗号。"""
    out = ""
    for p in parts:
        if not p:
            continue
        p = str(p).strip()
        if not p:
            continue
        if not out:
            out = p
            continue
        if out[-1] in "。！？；":
            out += p
        else:
            out += "，" + p
    return out


def asset_index(chapter, clip):
    """本条素材 id → @图片N，按 clip['assets'] 顺序连续编号。"""
    all_assets = {a["id"]: a for a in chapter["assets"]}
    idx = {}
    n = 0
    for aid in clip.get("assets", []):
        if aid not in all_assets:
            continue
        a = all_assets[aid]
        if a.get("kind") != "image":
            continue
        n += 1
        idx[aid] = "@图片{}".format(n)
    return idx, all_assets


def ref_of(asset_id, idx):
    return idx.get(asset_id, "") if asset_id else ""


def compose_character(ch, clip, idx):
    parts = []
    label = ch["name"]
    ref = ref_of(ch.get("asset"), idx)
    if ref:
        label = "{} {}".format(label, ref)
    parts.append(label)

    head = [ch["age"]] + list(ch.get("features", []))
    if ch.get("signature"):
        head.append(ch["signature"])
    line = "{}：{}".format(label, "，".join(head))
    return line


def compose_scene(scene, idx):
    ref = ref_of(scene.get("asset"), idx)
    label = "场景 {}".format(ref) if ref else "场景"
    text = "，".join([p for p in [scene["place"], scene["time"], scene["light"]] if p])
    return "{}：{}".format(label, text)


def compose_style(style):
    bits = [style["era"], style["look"], style["palette"], style["grain"]]
    bits += list(style.get("extra", []))
    return "风格：" + "，".join([b for b in bits if b])


def compose_visual(v):
    acts = v.get("action", "") + v.get("environment", "")
    props = []
    for p in v.get("props", []):
        core = p[:2]
        if core and core in acts:
            continue
        props.append(p)
    return join_text([v.get("action", ""), v.get("environment", ""), "；".join(props), v.get("light", "")])


def compose_dialogue(dialogue):
    if not dialogue:
        return "{}"
    out = []
    for dl in dialogue:
        lang = dl.get("lang", "zh")
        tone = dl.get("tone", "").strip()
        prefix = "画外" if dl.get("voiceover") else ""
        if lang not in ("zh", "cn"):
            verb = "用{}{}，{}说道".format(prefix, LANG_NAME.get(lang, lang), tone) if tone else "用{}{}说道".format(prefix, LANG_NAME.get(lang, lang))
        else:
            verb = prefix + tone + "说" if tone else prefix + "说"
        out.append("{}{}{{{}}}".format(dl["speaker"], verb, dl["text"]))
    return "；".join(out)


def compose_camera(cam):
    if cam.get("text"):
        return cam["text"]
    subject = cam.get("subject", "")
    return "{} + {}{}".format(subject, cam["height"], cam["angle"]).strip()


def compose_movement(m):
    phrase = STAB_PHRASE.get(m["stability"])
    return m["movement"] + ("，" + phrase if phrase else "")


def render_shot(s):
    f = s["focal"]
    lines = [
        "【镜头{}】".format(s["no"]),
        "焦距：{}mm {}，{}".format(f["mm"], f["type"], f["dof"]),
        "机位：" + compose_camera(s["camera"]),
        "构图：" + s["composition"],
        "运镜：" + compose_movement(s),
        "景别：" + s["shot_size"],
        "画面：" + compose_visual(s["visual"]),
        "台词：" + compose_dialogue(s.get("dialogue", [])),
        "视线落点：" + s.get("eyeline", ""),
        "焦点：" + s.get("focus", ""),
    ]
    if s.get("sfx"):
        lines.append("音效：" + "；".join("<{}>".format(x) for x in s["sfx"]))
    return lines


def build_constraints(chapter):
    cons = [c.rstrip("。") for c in chapter.get("constraints", [])]
    blob = "；".join(cons)
    st = chapter.get("style", {})
    bits = [st.get("era", ""), st.get("look", ""), st.get("palette", ""), st.get("grain", "")]
    bits += list(st.get("extra", []))
    style_line = "整体画面为" + "，".join([b for b in bits if b])
    if st.get("look") and st["look"] not in blob:
        cons.insert(0, style_line)
    required = [
        ("保持无字幕，避免生成任何文字或字幕", ["无字幕"]),
        ("台词仅人声，画面不出现字幕文字", ["人声"]),
        ("不要生成 Logo、水印", ["Logo", "水印"]),
        ("人物面部稳定不变形，禁止同款分身或双胞胎", ["双胞胎"]),
    ]
    for text, kws in required:
        if not any(k in blob for k in kws):
            cons.append(text)
    return cons


def render_clip(chapter, clip, header=False):
    idx, all_assets = asset_index(chapter, clip)
    chars = {c["id"]: c for c in chapter["characters"]}
    scenes = {s["id"]: s for s in chapter["scenes"]}
    lines = []

    if header:
        lines.append("Clip {:02d}｜{}｜{}s｜{}｜{}".format(
            clip["no"], chapter["meta"].get("model", "Seedance 2.0"),
            int(clip["duration_s"]) if float(clip["duration_s"]).is_integer() else clip["duration_s"],
            clip["ratio"], chapter["style"]["look"]))
        lines.append("")

    for aid in clip.get("assets", []):
        if aid in all_assets and all_assets[aid].get("kind") == "image":
            lines.append("{} = {}".format(idx[aid], all_assets[aid]["name"]))

    lines.append("")
    lines.append("【人物与场景】")
    scene = scenes.get(clip.get("scene"))
    if scene:
        lines.append(compose_scene(scene, idx))
    for cid in clip.get("cast", []):
        if cid in chars:
            lines.append(compose_character(chars[cid], clip, idx))
    cast_names = [chars[c]["name"] for c in clip.get("cast", []) if c in chars]
    lines.append("本条在场：" + "、".join(cast_names))

    for s in clip["shots"]:
        lines.append("")
        lines.extend(render_shot(s))

    if clip.get("ambience"):
        lines.append("")
        lines.append("<{}>".format(clip["ambience"]))
    if clip.get("music"):
        lines.append("（{}）".format(clip["music"]))

    cons = build_constraints(chapter)
    lines.append("")
    lines.append("【约束】")
    for i, c in enumerate(cons, 1):
        lines.append("{}. {}".format(i, c))

    return "\n".join(lines) + "\n"


def check_chapter(chapter, only_clip=None):
    errs, warns = [], []
    m = chapter.get("meta", {})

    # schema
    try:
        import jsonschema
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        validator = jsonschema.Draft7Validator(schema)
        for e in sorted(validator.iter_errors(chapter), key=lambda x: list(x.path)):
            errs.append("结构：{} ｜ {}".format("/".join(map(str, e.path)) or "root", e.message))
    except ImportError:
        warns.append("未安装 jsonschema，跳过结构校验")
    except FileNotFoundError:
        warns.append("未找到 schema.json，跳过结构校验")

    chars = {c["id"]: c for c in chapter.get("characters", [])}
    scenes = {s["id"]: s for s in chapter.get("scenes", [])}
    assets = {a["id"]: a for a in chapter.get("assets", [])}

    # 章级
    cons = "；".join(chapter.get("constraints", []))
    for kw, msg in [
        ("无字幕", "缺少字幕约束：保持无字幕"),
        ("Logo", "缺少 Logo 约束：不要生成 Logo"),
        ("水印", "缺少水印约束：不要生成水印"),
        ("人声", "缺少台词约束：台词仅人声"),
        ("双胞胎", "缺少防重复约束：禁止同款分身或双胞胎"),
    ]:
        if kw not in cons:
            errs.append("章级约束：" + msg)
    if not chapter.get("style"):
        errs.append("章级：缺少 style 风格锚点")

    name_seen = {}
    for c in chapter.get("characters", []):
        if c["name"] in name_seen:
            errs.append("人物重名：{}".format(c["name"]))
        name_seen[c["name"]] = c["id"]
        if not c.get("age"):
            errs.append("人物 {}：缺少年龄锚点".format(c["name"]))
        if len(c.get("features", [])) < 2:
            errs.append("人物 {}：静态特征少于 2 项".format(c["name"]))
        if c.get("asset") is None and len(c.get("features", [])) < 3:
            warns.append("无图人物 {}：建议写全 年龄 + 五官 + 服装 三项".format(c["name"]))
        if c.get("asset") and c["asset"] not in assets:
            errs.append("人物 {}：asset {} 不存在于素材库".format(c["name"], c["asset"]))

    clips = chapter.get("clips", [])
    for clip in clips:
        if only_clip and clip["no"] != only_clip:
            continue
        tag = "Clip{:02d}".format(clip["no"])

        if clip.get("scene") not in scenes:
            errs.append("{}：scene {} 未定义".format(tag, clip.get("scene")))

        for cid in clip.get("cast", []):
            if cid not in chars:
                errs.append("{}：cast 里的 {} 未定义".format(tag, cid))
        for aid in clip.get("assets", []):
            if aid not in assets:
                errs.append("{}：assets 里的 {} 未定义".format(tag, aid))

        imgs = [a for a in clip.get("assets", []) if a in assets and assets[a]["kind"] == "image"]
        if len(imgs) > 9:
            errs.append("{}：图片 {} 张，超过 9 张上限".format(tag, len(imgs)))

        # 有图人物未上传
        for cid in clip.get("cast", []):
            if cid in chars and chars[cid].get("asset") and chars[cid]["asset"] not in clip.get("assets", []):
                warns.append("{}：{} 有参考图但本条未上传".format(tag, chars[cid]["name"]))

        shots = clip.get("shots", [])
        if not shots:
            errs.append("{}：没有镜头".format(tag))
        if len(shots) > 6:
            errs.append("{}：镜头 {} 个，超过 6 个".format(tag, len(shots)))

        for s in shots:
            sn = "{}·镜{}".format(tag, s["no"])
            hits = [k for k in MOVE_KEYS if k in s["movement"]]
            if len(hits) > 1:
                errs.append("{}：运镜叠加「{}」，一镜只允许 1 种".format(sn, s["movement"]))

            vis = compose_visual(s["visual"])
            if not any(w in vis for w in PLACE_WORDS):
                errs.append("{}：画面缺方位（谁在画面哪个位置、朝哪、离什么多远）".format(sn))
            if not any(w in vis for w in LIGHT_WORDS):
                errs.append("{}：画面缺光线（光从哪来、明暗、有无投影）".format(sn))
            env = s["visual"].get("environment", "") + "".join(s["visual"].get("props", []))
            if not env.strip() and not any(w in vis for w in ENV_WORDS):
                errs.append("{}：画面缺环境或道具细节".format(sn))
            if len(vis) < 40:
                warns.append("{}：画面只有 {} 字，容易糊".format(sn, len(vis)))
            if len(vis) > 150:
                warns.append("{}：画面 {} 字，超过 150 字预算".format(sn, len(vis)))

            for dl in s.get("dialogue", []):
                if not dl.get("tone", "").strip():
                    errs.append("{}：台词「{}」缺少语气，写成「说话人+语气+说」".format(sn, dl["text"][:12]))
                if dl["speaker"] not in [chars[c]["name"] for c in clip.get("cast", []) if c in chars]:
                    errs.append("{}：台词说话人「{}」不在本条 cast".format(sn, dl["speaker"]))
                if dl["text"].count("{") != dl["text"].count("}"):
                    errs.append("{}：台词花括号未闭合".format(sn))
                if re.search(r"[A-Za-z]{2,}", dl["text"]) and re.search(r"[\u4e00-\u9fff]", dl["text"]):
                    warns.append("{}：台词中英混用".format(sn))

        tones = [dl.get("tone", "") for s in shots for dl in s.get("dialogue", [])]
        if len(tones) > 1 and len(set(tones)) == 1:
            warns.append("{}：全条 {} 句台词语气都是「{}」，缺少变化".format(tag, len(tones), tones[0]))

        text = render_clip(chapter, clip)
        if "TODO" in text:
            errs.append("{}：仍有 TODO 占位符未填写".format(tag))
        if re.search(r"\d+\s*[–\-~至]\s*\d+\s*(秒|s)|第\s*\d+\s*秒", text):
            errs.append("{}：出现秒数或时间戳，2.0 不支持".format(tag))
        if chapter.get("style", {}).get("look") and chapter["style"]["look"] not in text:
            errs.append("{}：风格锚点没有落到任何约束里".format(tag))
        if len(text) > 1600:
            warns.append("{}：提示词 {} 字，超过 1600 字建议上限".format(tag, len(text)))

        # 符号检查
        for line in text.splitlines():
            if "（" in line and not line.startswith("（"):
                errs.append("{}：全角括号只允许出现在配乐行｜{}".format(tag, line[:20]))
            if ("{" in line or "}" in line) and not line.startswith("台词："):
                errs.append("{}：花括号只允许出现在台词行｜{}".format(tag, line[:20]))
            if ("<" in line or ">" in line) and not (line.startswith("<") or line.startswith("音效：")):
                errs.append("{}：尖括号只允许出现在音效行｜{}".format(tag, line[:20]))

    # 章级多样性
    sizes = sorted({s["shot_size"] for c in clips for s in c["shots"]})
    mms = sorted({s["focal"]["mm"] for c in clips for s in c["shots"]})
    if len(sizes) < 2 and sum(len(c["shots"]) for c in clips) > 1:
        warns.append("全章景别单一：{}，缺少视觉变化".format(sizes))
    if len(mms) < 2 and sum(len(c["shots"]) for c in clips) > 1:
        warns.append("全章焦段单一：{}，视觉语言扁平".format(mms))

    # ---- 条与条相接：末镜静默垫片 / 首镜复用上条末镜 ----
    for i, clip in enumerate(clips):
        tag = "Clip{:02d}".format(clip["no"])
        shots = clip["shots"]
        last = shots[-1]
        if (not last.get("dialogue")
                and last["shot_size"] in ("近景", "特写", "中近景")
                and "固定" in last["movement"]):
            warns.append("{} 末镜：无声近景配固定镜头，像凑时长的静默垫片；补一句台词、改成收束动作，或把这条提前收在上一个镜头".format(tag))
        if i + 1 < len(clips):
            nxt = clips[i + 1]["shots"][0]
            a, b = last.get("camera", {}), nxt.get("camera", {})
            sa = a.get("subject_ref") or a.get("subject")
            sb = b.get("subject_ref") or b.get("subject")
            same_person = sa and sa == sb
            if same_person and last["shot_size"] == nxt["shot_size"]:
                ntag = "Clip{:02d}".format(clips[i + 1]["no"])
                if a.get("angle") == b.get("angle"):
                    errs.append("{} 末镜与 {} 首镜同人同景别同角度（{} / {} / {}）：跨条重复，换人、换角度或换景别".format(tag, ntag, sa, nxt["shot_size"], a.get("angle")))
                else:
                    warns.append("{} 末镜与 {} 首镜同人同景别（{} / {}）：相接重复".format(tag, ntag, sa, nxt["shot_size"]))

    # ---- 在场连续性：空镜后不许直接切到满场 ----
    EMPTY_WORDS = ["空着", "空无一人", "空荡荡", "空荡", "没有人", "没人", "无人", "一个人也没有", "桌边没人"]
    GROUP_WORDS = ["两人", "三人", "四人", "两个人", "三个人", "四个人", "几个人", "众人", "坐满", "一桌人", "满屋人"]
    ENTER_WORDS = ["走进", "走入", "推门", "进门", "入座", "落座", "坐下", "下楼", "踏进",
                   "踏入", "进入", "来到", "走向", "迈步", "跨进", "跨过", "进门", "起身", "站起",
                   "围坐", "落坐"]
    all_names = [c["name"] for c in chapter.get("characters", [])]

    def vis_text(s):
        v = s.get("visual", {})
        return v.get("action", "") + "，" + v.get("environment", "")

    def entry_near(text, token, span=16):
        start = 0
        while True:
            i = text.find(token, start)
            if i < 0:
                return False
            w = text[max(0, i - span):i + len(token) + span]
            if any(e in w for e in ENTER_WORDS):
                return True
            start = i + 1

    def is_empty_shot(s):
        t = vis_text(s)
        if any(n in t for n in all_names):
            return False
        return any(w in t for w in EMPTY_WORDS)

    def is_populated(s):
        t = vis_text(s)
        return bool([n for n in all_names if n in t] or [g for g in GROUP_WORDS if g in t])

    pairs = []
    for ci, clip in enumerate(clips):
        ctag = "Clip{:02d}".format(clip["no"])
        for i in range(len(clip["shots"]) - 1):
            pairs.append((clip["shots"][i], clip["shots"][i + 1], ctag, "同条"))
        if ci + 1 < len(clips) and clips[ci + 1].get("scene") == clip.get("scene"):
            ntag = "Clip{:02d}".format(clips[ci + 1]["no"])
            pairs.append((clip["shots"][-1], clips[ci + 1]["shots"][0], ctag + "→" + ntag, "跨条"))

    for prev, nxt, tag, kind in pairs:
        if not is_empty_shot(prev) or not is_populated(nxt):
            continue
        t = vis_text(nxt)
        missing = []
        for g in GROUP_WORDS:
            if g in t and not entry_near(t, g):
                missing.append(g)
        for n in all_names:
            if n in t and not entry_near(t, n):
                missing.append(n)
        if missing:
            errs.append("{}（{}）空镜后直接切到已有人物：{} 没有就近的进场/入座动作；首镜写「空着」后，不许下一镜直接「三人已坐」或「人已在桌边」，要么首镜就写出在场人物，要么让每个人在本镜可见进入（推门、下楼、走进、入座）".format(tag, kind, "、".join(sorted(set(missing))))) 
        else:
            warns.append("{}（{}）空镜后接人物进场，确认进场动作覆盖了画面里出现的每个人".format(tag, kind))

    # 对话段不许用空镜起手：15 秒里没有多余镜头留给纯空间建立
    for clip in clips:
        if only_clip and clip["no"] != only_clip:
            continue
        shots = clip.get("shots", [])
        if shots and any(s.get("dialogue") for s in shots) and is_empty_shot(shots[0]):
            errs.append("Clip{:02d}：首镜是空镜，但本条有台词；对话段的首镜必须带在场人物，删掉空镜，直接从人物或人物进场起镜".format(clip["no"]))

    # ---- 位移连续性：换空间必须有可见转移动作 ----
    PLACE = [
        ("车内", ["车内", "驾驶位", "副驾", "后座", "车厢"]),
        ("车外", ["车门外", "车外", "车边", "车门旁"]),
        ("门内", ["门内", "屋内", "屋里", "室内", "房中"]),
        ("门外", ["门廊", "门外", "屋外", "室外", "院外"]),
        ("船上", ["船头", "船尾", "船舱", "船上"]),
        ("岸上", ["岸上", "河岸", "土路", "岸边", "码头"]),
    ]
    PAIRS = {("车内", "车外"), ("门内", "门外"), ("船上", "岸上")}
    TRANS = ["下车", "上车", "推门", "拉门", "开门", "关门", "迈", "跨", "走进", "走出",
             "踏进", "踏出", "跳上", "跳下", "钻出", "翻过", "起身", "站起", "进门", "出门"]

    def place_of(text, name):
        idx = text.find(name)
        if idx < 0:
            return None
        best = None
        for cat, toks in PLACE:
            for t in toks:
                p = text.find(t, max(0, idx - 10), idx + len(name) + 14)
                if p < 0:
                    continue
                d = abs(p - idx)
                if best is None or len(t) > best[1] or (len(t) == best[1] and d < best[0]):
                    best = (d, len(t), cat)
        return best[2] if best else None

    last_place, last_text = {}, {}
    for clip in clips:
        tag = "Clip{:02d}".format(clip["no"])
        for shot in clip["shots"]:
            txt = shot["visual"].get("action", "") + "，" + shot["visual"].get("environment", "")
            for cid, c in chars.items():
                if c["name"] not in txt:
                    continue
                cur = place_of(txt, c["name"])
                if cur is None:
                    continue
                old = last_place.get(cid)
                if old and cur != old and (old, cur) in PAIRS:
                    prev = last_text.get(cid, "")
                    if not any(t in txt or t in prev for t in TRANS):
                        errs.append("{} 镜{}：{} 从「{}」切到「{}」，中间没有可见转移动作（下车/推门/迈步/跨门槛/跳上船），不能靠观众脑补".format(tag, shot["no"], c["name"], old, cur))
                last_place[cid] = cur
                last_text[cid] = txt

    return errs, warns


def main():
    ap = argparse.ArgumentParser(description="小说转分镜 · Seedance 2.0 渲染器")
    ap.add_argument("json_path", help="章级 JSON")
    ap.add_argument("--clip", type=int, help="只渲染第 N 条 Clip")
    ap.add_argument("--all", action="store_true", help="渲染全部 Clip")
    ap.add_argument("--list", action="store_true", help="列出所有 Clip")
    ap.add_argument("--check", action="store_true", help="只校验")
    ap.add_argument("--fence", action="store_true", help="套 ```text 代码围栏")
    ap.add_argument("--header", action="store_true", help="输出 Clip 头行")
    ap.add_argument("-o", "--out", help="输出文件或目录")
    args = ap.parse_args()

    chapter = load(args.json_path)
    clips = chapter.get("clips", [])

    if args.list:
        for c in clips:
            print("Clip {:02d}｜{}s｜{}｜{} 镜｜cast {}".format(
                c["no"], int(c["duration_s"]), c["ratio"], len(c["shots"]),
                "、".join(c.get("cast", []))))
        return

    errs, warns = check_chapter(chapter, args.clip if not args.all else None)
    for e in errs:
        print("[硬伤]", e, file=sys.stderr)
    for w in warns:
        print("[提醒]", w, file=sys.stderr)

    if args.check:
        print("\n校验完成：{} 处硬伤，{} 处提醒".format(len(errs), len(warns)))
        sys.exit(1 if errs else 0)

    if args.clip:
        targets = [c for c in clips if c["no"] == args.clip]
        if not targets:
            print("找不到 Clip {}".format(args.clip), file=sys.stderr)
            sys.exit(1)
    elif args.all:
        targets = clips
    elif len(clips) == 1:
        targets = clips
    else:
        print("本章有 {} 条 Clip，请用 --clip N 指定，或 --all 全出，--list 查看".format(len(clips)), file=sys.stderr)
        sys.exit(1)

    outs = [render_clip(chapter, c, header=args.header) for c in targets]
    text = "\n".join(outs)
    if args.fence:
        text = "```text\n" + text.rstrip("\n") + "\n```\n"

    if args.out:
        if len(targets) > 1 and (args.out.endswith(os.sep) or os.path.isdir(args.out)):
            os.makedirs(args.out, exist_ok=True)
            for c, t in zip(targets, outs):
                p = os.path.join(args.out, "clip_{:02d}.txt".format(c["no"]))
                with open(p, "w", encoding="utf-8") as f:
                    f.write(t)
                print("已写入 {}（{} 字）".format(p, len(t)))
        else:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print("已写入 {}（{} 字）".format(args.out, len(text)))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
