import os
import re
import shutil
import subprocess
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

try:
    from mutagen.id3 import ID3, TALB, TIT2, TPE1, USLT
    from mutagen.mp3 import MP3

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("Warning: mutagen not installed. Install with: pip install mutagen")

try:
    import syncedlyrics

    SYNCEDLYRICS_AVAILABLE = True
except ImportError:
    SYNCEDLYRICS_AVAILABLE = False
    print("Warning: syncedlyrics not installed. Install with: pip install syncedlyrics")


def extract_metadata(file_path):
    if not MUTAGEN_AVAILABLE:
        return None, None, None, None

    try:
        audio = MP3(file_path, ID3=ID3)

        if not audio.tags:
            return None, None, None, None

        def get_text_frame(tag_key):
            frame = audio.tags.get(tag_key)
            if frame is None:
                return None
            if hasattr(frame, "text") and frame.text:
                return str(frame.text[0])
            return str(frame) if frame else None

        title = get_text_frame("TIT2")
        artist = get_text_frame("TPE1")
        album = get_text_frame("TALB")
        duration = int(audio.info.length) if audio.info else None

        title = title if title else None
        artist = artist if artist else None
        album = album if album else None

        return title, artist, album, duration
    except Exception as e:
        print(f"      Metadata read error: {e}")
        return None, None, None, None


def parse_filename(filename):
    stem = Path(filename).stem
    stem = stem.strip()

    stem = re.sub(r"[\u2010-\u2015\u2212]", "-", stem)

    stem = re.sub(
        r"^\s*(?:(?:cd|disc|disk)\s*\d+\s*[-._\s]*)?"
        r"(?:\d{1,3}(?:[._-]\d{1,3})?\s*[-._]+\s*)",
        "",
        stem,
        flags=re.IGNORECASE,
    ).strip()

    stem = re.sub(r"\s*[\(\[][^)\]]+[\)\]]\s*$", "", stem).strip()

    m = re.split(r"\s+-\s+", stem, maxsplit=1)
    if len(m) == 2 and m[0] and m[1]:
        return m[1].strip(), m[0].strip()

    return stem.strip(), None


def check_existing_lyrics(file_path):
    if not MUTAGEN_AVAILABLE:
        return False

    try:
        audio = MP3(file_path, ID3=ID3)

        if not audio.tags:
            return False

        return any(k.startswith("USLT") for k in audio.tags.keys())
    except Exception as e:
        print(f"      Lyrics check error: {e}")
        return None


def fetch_lyrics(title, artist, retries=2, backoff_seconds=1.0):
    if not SYNCEDLYRICS_AVAILABLE or not title:
        return None

    search_query = f"{title} {artist}" if artist else title
    attempts = retries + 1

    for attempt in range(1, attempts + 1):
        try:
            lyrics = syncedlyrics.search(
                search_query,
                synced_only=True,
                providers=["Lrclib", "NetEase", "Megalobiz"],
            )
            return lyrics
        except Exception as e:
            print(f"      Lyrics fetch error (attempt {attempt}/{attempts}): {e}")
            if attempt < attempts:
                time.sleep(backoff_seconds * attempt)

    return None


def embed_lyrics(file_path, lyrics_text):
    if not MUTAGEN_AVAILABLE or not lyrics_text:
        return False

    try:
        audio = MP3(file_path, ID3=ID3)

        if audio.tags is None:
            audio.add_tags()

        existing_uslt = [
            f
            for f in audio.tags.getall("USLT")
            if not (getattr(f, "lang", None) == "eng")
        ]
        audio.tags.setall(
            "USLT",
            existing_uslt + [USLT(encoding=1, lang="eng", desc="", text=lyrics_text)],
        )
        audio.tags.update_to_v23()
        audio.save(v2_version=3)
        return True
    except Exception as e:
        print(f"      Lyrics embed error: {e}")
        return False


def process_lyrics_for_file(file_path, filename):
    if not MUTAGEN_AVAILABLE or not SYNCEDLYRICS_AVAILABLE:
        return "skipped_no_libs"

    existing = check_existing_lyrics(file_path)
    if existing is None:
        print(f"      Skipping lyrics due to tag read error: {filename}")
        return "skipped_tag_error"
    if existing:
        print(f"      Lyrics already exist: {filename}")
        return "skipped_exists"

    title, artist, album, duration = extract_metadata(file_path)

    if not title or not artist:
        parsed_title, parsed_artist = parse_filename(filename)
        title = title or parsed_title
        artist = artist or parsed_artist

    if not title:
        print(f"      Could not determine title: {filename}")
        return "failed_no_title"

    print(f"      Fetching lyrics: {title}" + (f" - {artist}" if artist else ""))
    lyrics = fetch_lyrics(title, artist)

    if not lyrics:
        print("      No lyrics found")
        return "failed_not_found"

    if embed_lyrics(file_path, lyrics):
        print("      Lyrics embedded successfully")
        return "success"
    else:
        return "failed_embed"


def convert_audio_to_mp3(convert_workers_override=None, lyrics_workers_override=None):
    audio_extensions = {
        ".opus",
        ".flac",
        ".wav",
        ".aac",
        ".m4a",
        ".ogg",
        ".wma",
        ".mp4",
        ".m4p",
        ".aiff",
        ".ape",
        ".mpc",
        ".tta",
        ".wv",
        ".alac",
        ".dsd",
        ".dsf",
        ".dff",
        ".tak",
        ".3gp",
        ".amr",
        ".ac3",
        ".dts",
        ".mp2",
        ".ra",
        ".rm",
        ".voc",
        ".au",
        ".snd",
        ".webm",
    }

    current_dir = Path.cwd()
    compressed_dir = current_dir / "compressed"

    compressed_dir.mkdir(exist_ok=True)

    cpu_count = os.cpu_count() or 4
    default_convert_workers = max(1, cpu_count - 2)
    convert_workers = (
        convert_workers_override
        if convert_workers_override is not None
        else default_convert_workers
    )
    lyrics_workers = (
        lyrics_workers_override if lyrics_workers_override is not None else 4
    )

    converted_count = 0
    copied_count = 0
    skipped_count = 0
    error_count = 0

    lyrics_stats = {
        "success": 0,
        "failed_not_found": 0,
        "failed_no_title": 0,
        "failed_embed": 0,
        "skipped_exists": 0,
        "skipped_no_libs": 0,
        "skipped_tag_error": 0,
    }

    print("Audio to MP3 Converter Started")
    print(f"Source: {current_dir}")
    print(f"Output: {compressed_dir}")

    if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
        print("Lyrics embedding: ENABLED")
    else:
        print("Lyrics embedding: DISABLED (missing libraries)")
    print(f"Conversion workers: {convert_workers}")
    if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
        print(f"Lyrics workers: {lyrics_workers}")

    print("-" * 50)

    counts_lock = Lock()
    print_lock = Lock()

    def safe_print(msg):
        with print_lock:
            print(msg)

    def update_lyrics_stats(result):
        if result in lyrics_stats:
            with counts_lock:
                lyrics_stats[result] += 1

    def run_lyrics(output_file, filename):
        result = process_lyrics_for_file(output_file, filename)
        if result == "skipped_tag_error":
            time.sleep(0.5)
            result = process_lyrics_for_file(output_file, filename)
        update_lyrics_stats(result)

    def convert_file(input_file, output_file, filename, lyrics_pool):
        nonlocal converted_count, error_count
        try:
            cmd = [
                "ffmpeg",
                "-i",
                str(input_file),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-map_metadata",
                "0",
                "-id3v2_version",
                "3",
                "-vn",
                "-y",
                str(output_file),
            ]

            subprocess.run(cmd, capture_output=True, text=True, check=True)

            safe_print(f"   Converted: {filename}")
            with counts_lock:
                converted_count += 1

            if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
                lyrics_pool.submit(run_lyrics, output_file, filename)
        except subprocess.CalledProcessError as e:
            safe_print(f"   Error converting {filename}: {e}")
            if e.stderr:
                safe_print(f"   FFmpeg error: {e.stderr.strip()}")
            with counts_lock:
                error_count += 1
        except Exception as e:
            safe_print(f"   Unexpected error with {filename}: {e}")
            with counts_lock:
                error_count += 1

    def copy_file(input_file, output_file, filename, lyrics_pool):
        nonlocal copied_count, error_count
        try:
            shutil.copy2(input_file, output_file)
            safe_print(f"   Copied: {filename}")
            with counts_lock:
                copied_count += 1

            if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
                lyrics_pool.submit(run_lyrics, output_file, filename)
        except Exception as e:
            safe_print(f"   Error copying {filename}: {e}")
            with counts_lock:
                error_count += 1

    futures = []

    with (
        ThreadPoolExecutor(max_workers=convert_workers) as convert_pool,
        ThreadPoolExecutor(max_workers=lyrics_workers) as lyrics_pool,
    ):
        for root, _, files in os.walk(current_dir):
            root_path = Path(root)

            if compressed_dir in root_path.parents or root_path == compressed_dir:
                continue

            audio_files = []
            mp3_files = []
            for f in files:
                suffix = Path(f).suffix.lower()
                if suffix in audio_extensions:
                    audio_files.append(f)
                elif suffix == ".mp3":
                    mp3_files.append(f)

            if audio_files or mp3_files:
                rel_path = root_path.relative_to(current_dir)
                output_dir = compressed_dir / rel_path
                output_dir.mkdir(parents=True, exist_ok=True)

                safe_print(f"\nProcessing: {rel_path}")

                for filename in audio_files:
                    input_file = root_path / filename
                    output_file = output_dir / f"{Path(filename).stem}.mp3"

                    if output_file.exists():
                        safe_print(f"   Skip: {filename} (already exists)")
                        with counts_lock:
                            skipped_count += 1
                        continue

                    futures.append(
                        convert_pool.submit(
                            convert_file, input_file, output_file, filename, lyrics_pool
                        )
                    )

                for filename in mp3_files:
                    input_file = root_path / filename
                    output_file = output_dir / filename

                    if output_file.exists():
                        safe_print(f"   Skip: {filename} (already exists)")
                        with counts_lock:
                            skipped_count += 1
                        continue

                    futures.append(
                        convert_pool.submit(
                            copy_file, input_file, output_file, filename, lyrics_pool
                        )
                    )

        for f in as_completed(futures):
            _ = f.result()

    print("\n" + "=" * 50)
    print("CONVERSION COMPLETE")
    print(f"Files converted: {converted_count}")
    print(f"Files copied: {copied_count}")
    print(f"Files processed: {converted_count + copied_count}")
    print(f"Files skipped: {skipped_count}")
    print(f"Errors: {error_count}")
    print(f"Output location: {compressed_dir}")

    if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
        print("\n" + "=" * 50)
        print("LYRICS EMBEDDING SUMMARY")
        print(f"Successfully embedded: {lyrics_stats.get('success', 0)}")
        print(f"Not found: {lyrics_stats.get('failed_not_found', 0)}")
        print(f"Already had lyrics: {lyrics_stats.get('skipped_exists', 0)}")
        print(f"No title found: {lyrics_stats.get('failed_no_title', 0)}")
        if lyrics_stats.get("skipped_tag_error", 0) > 0:
            print(f"Tag read errors: {lyrics_stats.get('skipped_tag_error', 0)}")
        if lyrics_stats.get("failed_embed", 0) > 0:
            print(f"Embed failed: {lyrics_stats.get('failed_embed', 0)}")

    if error_count > 0:
        print(
            "\nSome files had errors. Check FFmpeg installation and file permissions."
        )

    if converted_count == 0 and skipped_count == 0:
        print("\nNo audio files found in current directory or subdirectories.")


def check_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert audio files to optimized MP3 and optionally embed synced lyrics."
    )
    parser.add_argument(
        "-workers",
        "--workers",
        type=int,
        default=None,
        help="Number of parallel conversion/copy workers (default: cpu_count-2).",
    )
    parser.add_argument(
        "--lyrics-workers",
        type=int,
        default=None,
        help="Number of parallel lyrics workers (default: 4).",
    )
    args = parser.parse_args()

    print("Checking FFmpeg installation...")

    if not check_ffmpeg():
        print("FFmpeg not found!")
        print("Please install FFmpeg and make sure it's in your system PATH.")
        print("Download from: https://ffmpeg.org/download.html")
        exit(1)

    print("\nFFmpeg found!")

    if not MUTAGEN_AVAILABLE or not SYNCEDLYRICS_AVAILABLE:
        print("\nOptional: For lyrics embedding functionality, install:")
        if not MUTAGEN_AVAILABLE:
            print("   pip install mutagen")
        if not SYNCEDLYRICS_AVAILABLE:
            print("   pip install syncedlyrics")

    print("\n" + "=" * 50)
    print("IMPORTANT NOTICE")
    print("This script will process ALL audio files in the current directory")
    print("and subdirectories. Original files will NOT be modified.")
    print("Converted files will be saved to 'compressed' folder.")
    if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
        print("Synced lyrics will be embedded into MP3 files.")
    print("=" * 50)

    response = input("\nProceed? (y/N): ").strip().lower()

    if response == "y":
        convert_audio_to_mp3(args.workers, args.lyrics_workers)
    else:
        print("Operation cancelled.")
