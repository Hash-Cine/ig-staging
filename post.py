"""Post the next due Instagram reel from queue/. Run every 15 minutes by .github/workflows/post.yml.

A queue item is queue/<name>.json: {"asset": "<file on the ig-staging release>", "caption": "...", "post_at": ISO time}.
One reel per run, so a backlog goes out 15 minutes apart instead of all at once.
Posted items move to posted/ with the media id; a failure is recorded on the item and retried, at most 3 times.
Needs IG_ACCESS_TOKEN, IG_USER_ID, GITHUB_REPOSITORY and GH_TOKEN in the environment.
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://graph.facebook.com/v21.0/"
TOKEN, UID, REPO = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"], os.environ["GITHUB_REPOSITORY"]
MAX_TRIES = 3


def call(path, data=None):
    req = urllib.request.Request(API + path, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"Authorization": "Bearer " + TOKEN})
    try:
        return json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Graph API {exc.code}: {exc.read().decode(errors='replace')[:600]}") from None


def publish(job):
    url = f"https://github.com/{REPO}/releases/download/ig-staging/{urllib.parse.quote(job['asset'])}"
    cid = call(f"{UID}/media", {"media_type": "REELS", "video_url": url, "caption": job.get("caption", "")})["id"]
    for _ in range(100):
        st = call(f"{cid}?fields=status_code,status")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the video: {st.get('status')}")
        time.sleep(6)
    else:
        raise RuntimeError("Timed out waiting for Instagram to process the video")
    return {"media_id": call(f"{UID}/media_publish", {"creation_id": cid})["id"], "creation_id": cid}


def save(msg):
    subprocess.run(["git", "add", "-A", "queue", "posted"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], check=True)
    subprocess.run(["git", "push", "-q"], check=True)


def main():
    now = dt.datetime.now(dt.timezone.utc)
    for item in sorted(pathlib.Path("queue").glob("*.json")):
        job = json.loads(item.read_text(encoding="utf-8"))
        if job.get("tries", 0) >= MAX_TRIES or dt.datetime.fromisoformat(job["post_at"]) > now:
            continue
        try:
            job.update(result=publish(job), posted_at=now.isoformat(timespec="seconds"))
        except Exception as exc:
            job["tries"] = job.get("tries", 0) + 1
            job["last_error"] = str(exc).replace(TOKEN, "***")
            item.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
            save(f"failed {item.name} (try {job['tries']})")
            print("FAILED", item.name, job["last_error"])
            raise SystemExit(1)
        pathlib.Path("posted").mkdir(exist_ok=True)
        (pathlib.Path("posted") / item.name).write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        item.unlink()
        save(f"posted {item.name}")
        subprocess.run(["gh", "release", "delete-asset", "ig-staging", job["asset"], "-R", REPO, "-y"])
        print("posted", item.name, job["result"])
        return
    print("nothing due")


if __name__ == "__main__":
    main()
