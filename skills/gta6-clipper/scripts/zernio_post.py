#!/usr/bin/env python3
"""Schedule finished clips to Instagram / TikTok / YouTube Shorts / Facebook through Zernio.

    python zernio_post.py accounts
    python zernio_post.py schedule <run folder> [--accounts id,id] [--start 60] [--gap 120] [--draft] [--dry-run]

Needs ZERNIO_API_KEY (skills/gta6-clipper/.env or the environment). Zernio's free plan
includes 2 connected accounts. Each clip is uploaded (presign + PUT), then one post per clip
goes to every chosen account. Already-scheduled clips are tracked in clips/zernio.json and
never posted twice. Standard library only.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://zernio.com/api/v1"
SKILL = Path(__file__).resolve().parent.parent
PLATFORMS = {"instagram", "tiktok", "youtube", "facebook"}


def api_key():
    k = os.environ.get("ZERNIO_API_KEY")
    f = SKILL / ".env"
    if not k and f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ZERNIO_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"')
    if not k:
        sys.exit("No ZERNIO_API_KEY. Get one at zernio.com -> Settings -> API keys, "
                 "then add ZERNIO_API_KEY=... to " + str(f))
    return k


def call(method, path_or_url, body=None, key=None):
    url = path_or_url if path_or_url.startswith("http") else API + path_or_url
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"Zernio {method} {path_or_url} -> {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")


def upload(mp4, key):
    """Presign + PUT. The uploaded file lives 7 days unless a post using it publishes."""
    d = call("POST", "/media/presign", {"filename": mp4.name, "contentType": "video/mp4",
                                        "size": mp4.stat().st_size}, key)
    req = urllib.request.Request(d["uploadUrl"], data=mp4.read_bytes(), method="PUT",
                                 headers={"Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=600) as r:
        if r.status not in (200, 204):
            sys.exit(f"upload {mp4.name} -> {r.status}")
    return d["publicUrl"]


def parse_txt(txt):
    """The .txt render writes: TITLE: x / CAPTION: / caption / hashtags / Source: ..."""
    title, caption, tags, part = "", [], "", None
    for line in txt.read_text(encoding="utf-8").splitlines():
        if line.startswith("TITLE:"):
            title = line[6:].strip()
        elif line.startswith("CAPTION:"):
            part = "cap"
        elif line.startswith(("Source:", "Category:")):
            part = None
        elif part == "cap" and line.strip():
            if line.lstrip().startswith("#"):
                tags = line.strip()
            else:
                caption.append(line.strip())
    return title, " ".join(caption), tags


def build_post(title, caption, tags, video_url, accounts, when, draft):
    post = {"content": f"{caption}\n\n{tags}".strip(),
            "mediaItems": [{"type": "video", "url": video_url}],
            "platforms": []}
    for a in accounts:
        p = {"platform": a["platform"], "accountId": a["_id"]}
        if a["platform"] == "youtube":
            p["platformSpecificData"] = {"title": (title or caption)[:100], "visibility": "public"}
        post["platforms"].append(p)
    if any(a["platform"] == "tiktok" for a in accounts):
        post["tiktokSettings"] = {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True,
                                  "allow_duet": True, "allow_stitch": True,
                                  "content_preview_confirmed": True, "express_consent_given": True}
    if not draft:  # no scheduledFor and no publishNow = saved as a draft
        post["scheduledFor"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return post


def list_accounts(key):
    accs = call("GET", "/accounts", key=key).get("accounts", [])
    return [a for a in accs if a.get("platform") in PLATFORMS]


def cmd_accounts(a):
    for x in list_accounts(api_key()):
        print(f"{x['_id']}  {x['platform']:<10} {x.get('username') or x.get('displayName') or ''}")


def cmd_schedule(a):
    key = api_key()
    clips = Path(a.run) / "clips" if (Path(a.run) / "clips").is_dir() else Path(a.run)
    done_f = clips / "zernio.json"
    done = json.loads(done_f.read_text(encoding="utf-8")) if done_f.exists() else {}
    accs = list_accounts(key)
    if a.accounts:
        want = set(a.accounts.split(","))
        accs = [x for x in accs if x["_id"] in want]
    if not accs:
        sys.exit("No Instagram/TikTok/YouTube/Facebook account connected in Zernio (or none matched --accounts).")
    todo = [m for m in sorted(clips.glob("clip_*.mp4")) if m.name not in done]
    if not todo:
        print("Nothing new to schedule in", clips)
        return
    when = datetime.now(timezone.utc) + timedelta(minutes=a.start)
    for mp4 in todo:
        title, caption, tags = parse_txt(mp4.with_suffix(".txt"))
        nets = ", ".join(x["platform"] for x in accs)
        stamp = "draft" if a.draft else when.astimezone().strftime("%a %d %b %H:%M")
        if a.dry_run:
            print(f"[dry-run] {mp4.name} -> {nets} @ {stamp}: {title}")
        else:
            url = upload(mp4, key)
            res = call("POST", "/posts", build_post(title, caption, tags, url, accs, when, a.draft), key)
            pid = (res.get("post") or res).get("_id")
            done[mp4.name] = {"post_id": pid, "when": stamp, "networks": nets}
            done_f.write_text(json.dumps(done, indent=1), encoding="utf-8")
            print(f"{mp4.name} -> {nets} @ {stamp}  (post {pid})")
        when += timedelta(minutes=a.gap)
    print("Review or cancel any post in the Zernio dashboard before it goes out.")


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / "clip_01_x.txt"
        t.write_text("TITLE: Tim gets robbed\n\nCAPTION:\nHe did NOT see that coming\n\n#GTA6 #gta6clips\n\n"
                     "Source: Tim, https://x at 1:02:03\nCategory: funny  Score: 8  Layout: facecam\n",
                     encoding="utf-8")
        title, cap, tags = parse_txt(t)
        assert (title, cap, tags) == ("Tim gets robbed", "He did NOT see that coming", "#GTA6 #gta6clips")
    accs = [{"_id": "a", "platform": "tiktok"}, {"_id": "b", "platform": "youtube"}]
    now = datetime(2026, 11, 21, 17, 0, tzinfo=timezone.utc)
    p = build_post("T", "C", "#t", "https://u", accs, now, draft=False)
    assert p["scheduledFor"] == "2026-11-21T17:00:00Z" and "tiktokSettings" in p
    assert p["platforms"][1]["platformSpecificData"]["title"] == "T"
    assert "scheduledFor" not in build_post("T", "C", "#t", "https://u", accs, now, draft=True)
    print("self-check ok")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    sub.add_parser("self-check")
    s = sub.add_parser("schedule")
    s.add_argument("run", help="run folder (or its clips folder)")
    s.add_argument("--accounts", help="comma-separated Zernio account ids (default: all connected)")
    s.add_argument("--start", type=int, default=60, help="minutes from now for the first post (default 60)")
    s.add_argument("--gap", type=int, default=120, help="minutes between posts (default 120)")
    s.add_argument("--draft", action="store_true", help="save as drafts instead of scheduling")
    s.add_argument("--dry-run", action="store_true", help="show the plan, upload nothing")
    a = p.parse_args()
    {"accounts": cmd_accounts, "schedule": cmd_schedule, "self-check": lambda _: self_check()}[a.cmd](a)


if __name__ == "__main__":
    main()
