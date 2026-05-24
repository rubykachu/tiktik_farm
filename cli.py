"""Command-line interface.

Two modes:

  1. Flag-based (legacy, unchanged): `python cli.py login -n foo`,
     `python cli.py upload -u foo -v clip.mp4 -t "hi"`, etc.
     All existing scripts and CI invocations keep working.

  2. Interactive REPL (new): `python cli.py` with no subcommand drops into a
     prompt_toolkit-based shell with history, tab-completion, and commands
     matching the flag-based subcommands.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys

from tiktok_uploader import Video, tiktok
from tiktok_uploader.basics import eprint
from tiktok_uploader.Config import Config


# ---------- argparse surface (shared by flag-mode and REPL) -----------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TikTokAutoUpload CLI",
        prog="cli.py",
    )
    sub = parser.add_subparsers(dest="subcommand")

    lp = sub.add_parser("login", help="Log in and save session cookies")
    lp.add_argument("-n", "--name", required=True)

    up = sub.add_parser("upload", help="Upload a video to TikTok")
    up.add_argument("-u", "--users", required=True)
    up.add_argument("-v", "--video")
    up.add_argument("-yt", "--youtube")
    up.add_argument("-t", "--title", required=True)
    up.add_argument("-ct", "--comment", type=int, default=1, choices=[0, 1])
    up.add_argument("-d", "--duet", type=int, default=0, choices=[0, 1])
    up.add_argument("-st", "--stitch", type=int, default=0, choices=[0, 1])
    up.add_argument("-vi", "--visibility", type=int, default=0)
    up.add_argument("-bo", "--brandorganic", type=int, default=0)
    up.add_argument("-bc", "--brandcontent", type=int, default=0)
    up.add_argument("-ai", "--ailabel", type=int, default=0)
    up.add_argument("-p", "--proxy", default="")

    sp = sub.add_parser("show", help="List users and videos")
    sp.add_argument("-u", "--users", action="store_true")
    sp.add_argument("-v", "--videos", action="store_true")

    return parser


# ---------- command handlers ------------------------------------------------


def do_login(args) -> None:
    tiktok.login(args.name)


def do_upload(args) -> None:
    if args.video is None and args.youtube is None:
        eprint("No source provided. Use -v or -yt to provide video source.")
        sys.exit(1)
    if args.video and args.youtube:
        eprint("Both -v and -yt flags cannot be used together.")
        sys.exit(1)

    if args.youtube:
        video_obj = Video(args.youtube, args.title)
        video_obj.is_valid_file_format()
        args.video = video_obj.source_ref
    else:
        expected = os.path.join(os.getcwd(), Config.get().videos_dir, args.video)
        if not os.path.exists(expected) and not os.path.isabs(args.video):
            print("[-] Video does not exist")
            print("Video Names Available: ")
            video_dir = os.path.join(os.getcwd(), Config.get().videos_dir)
            for name in os.listdir(video_dir):
                print(f"[-] {name}")
            sys.exit(1)

    tiktok.upload_video(
        args.users, args.video, args.title, 0,
        args.comment, args.duet, args.stitch, args.visibility,
        args.brandorganic, args.brandcontent, args.ailabel, args.proxy,
    )


def do_show(args) -> None:
    if args.users:
        print("User Names logged in: ")
        cookie_dir = os.path.join(os.getcwd(), Config.get().cookies_dir)
        if os.path.isdir(cookie_dir):
            for name in os.listdir(cookie_dir):
                if name.startswith("tiktok_session-"):
                    print(f'[-] {name.split("tiktok_session-")[1].rsplit(".cookie", 1)[0]}')
    if args.videos:
        print("Video Names: ")
        video_dir = os.path.join(os.getcwd(), Config.get().videos_dir)
        if os.path.isdir(video_dir):
            for name in os.listdir(video_dir):
                print(f"[-] {name}")
    if not args.users and not args.videos:
        print("No flag provided. Use -u (show all cookies) or -v (show all videos).")


def dispatch(args) -> None:
    if args.subcommand == "login":
        do_login(args)
    elif args.subcommand == "upload":
        do_upload(args)
    elif args.subcommand == "show":
        do_show(args)
    else:
        eprint(f"Unknown subcommand: {args.subcommand}")


# ---------- REPL ------------------------------------------------------------


REPL_INTRO = (
    "TikTokAutoUpload interactive shell.\n"
    "Commands: login, upload, show, help, exit.\n"
    "Run any command with --help for its flags. Ctrl-D or 'exit' to quit.\n"
)


def _repl() -> None:
    # prompt_toolkit is optional — fall back to plain input() if missing so
    # the CLI still works in minimal environments.
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
    except ImportError:
        print("[prompt_toolkit not installed — using basic input()]")
        _repl_basic()
        return

    parser = build_parser()
    completer = WordCompleter(
        ["login", "upload", "show", "help", "exit",
         "-n", "-u", "-v", "-yt", "-t", "-ct", "-d", "-st", "-vi",
         "-bo", "-bc", "-ai", "-p", "--name", "--users", "--video",
         "--youtube", "--title"],
        ignore_case=True,
    )
    history_path = os.path.expanduser("~/.tiktok_uploader_history")
    session = PromptSession(history=FileHistory(history_path), completer=completer)
    print(REPL_INTRO)

    while True:
        try:
            line = session.prompt("tiktok> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            return
        if line == "help":
            parser.print_help()
            continue
        try:
            tokens = shlex.split(line)
            args = parser.parse_args(tokens)
        except SystemExit:
            # argparse calls sys.exit on --help or errors; catch so the REPL
            # doesn't die.
            continue
        try:
            dispatch(args)
        except SystemExit:
            continue
        except Exception as e:  # pragma: no cover - interactive feedback
            eprint(f"[-] {type(e).__name__}: {e}")


def _repl_basic() -> None:
    parser = build_parser()
    print(REPL_INTRO)
    while True:
        try:
            line = input("tiktok> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line or line in ("exit", "quit"):
            return
        try:
            args = parser.parse_args(shlex.split(line))
            dispatch(args)
        except SystemExit:
            continue


def main() -> None:
    # Match historical behavior: load config.txt on startup.
    if os.path.exists("./config.txt"):
        Config.load("./config.txt")

    if len(sys.argv) == 1:
        _repl()
        return

    parser = build_parser()
    args = parser.parse_args()
    if args.subcommand is None:
        parser.print_help()
        sys.exit(1)
    dispatch(args)


if __name__ == "__main__":
    main()
