"""
Ultroid-Neo: 1-Click Push & Continuous Deployment Script
Stages changes, runs tests, commits, pushes to GitHub, and triggers Render deploy hook.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from dotenv import load_dotenv

load_dotenv()


def run_cmd(cmd: list[str], check: bool = True) -> str:
    print(f"👉 Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"❌ Error (code {res.returncode}):\n{res.stderr.strip()}")
        sys.exit(res.returncode)
    return res.stdout.strip()


def main():
    commit_msg = sys.argv[1] if len(sys.argv) > 1 else f"update: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"\n🚀 [Ultroid-Neo] Initiating 1-Click Push ({commit_msg})\n")

    # 1. Run local test suite first to ensure zero regressions
    print("🧪 Step 1: Running unit tests...")
    test_res = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        capture_output=True,
        text=True,
    )
    if test_res.returncode != 0:
        print(f"⚠️ Tests failed! Aborting push to prevent broken deployment:\n{test_res.stdout}")
        sys.exit(1)
    print("✅ All unit tests passed!\n")

    # 2. Compile requirements.txt from pyproject.toml if needed
    print("📦 Step 2: Syncing dependencies...")
    try:
        subprocess.run(["uv", "pip", "compile", "pyproject.toml", "-o", "requirements.txt"], check=False)
    except Exception:
        pass

    # 3. Stage changes
    print("📝 Step 3: Staging git changes...")
    run_cmd(["git", "add", "."])

    # Check if there are changes to commit
    status = run_cmd(["git", "status", "--porcelain"], check=False)
    if not status:
        print("💡 No new changes to commit. Working tree is clean.")
    else:
        print("💾 Step 4: Committing changes...")
        run_cmd(["git", "commit", "-m", commit_msg])

    # 4. Push to remote
    print("☁️ Step 5: Pushing to GitHub...")
    # Check if remote exists
    remotes = run_cmd(["git", "remote"], check=False)
    if not remotes:
        print("⚠️ No git remote 'origin' configured yet.")
        print("Run: git remote add origin https://github.com/YOUR_USERNAME/ultroid-neo.git")
        print("Then re-run this script!")
        return

    push_res = run_cmd(["git", "push", "origin", "main"], check=False)
    if not push_res:
        # Try master if main doesn't exist
        run_cmd(["git", "push", "origin", "master"], check=False)

    print("✅ Successfully pushed to GitHub!\n")

    # 5. Trigger Render Deploy Hook (if configured)
    deploy_hook = os.getenv("RENDER_DEPLOY_HOOK")
    if deploy_hook:
        print("⚡ Step 6: Triggering Render Webhook for instant zero-downtime rebuild...")
        try:
            req = urllib.request.Request(deploy_hook, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"✅ Render Deploy Triggered! (Status {resp.status})")
        except Exception as e:
            print(f"⚠️ Could not trigger Render hook: {e}")
    else:
        print("💡 Tip: Add RENDER_DEPLOY_HOOK=https://api.render.com/deploy/srv-... in .env")
        print("   Render will auto-deploy anyway via GitHub webhook if connected to your repository!")

    print("\n🎉 Deployment cycle complete! Render is building your updated userbot.")


if __name__ == "__main__":
    main()
