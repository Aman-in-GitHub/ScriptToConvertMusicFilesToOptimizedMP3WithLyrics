import os
import subprocess
import shutil
import time
import re
from pathlib import Path

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, USLT, TIT2, TPE1, TALB

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

        title = str(audio.tags.get("TIT2", ""))
        artist = str(audio.tags.get("TPE1", ""))
        album = str(audio.tags.get("TALB", ""))
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
    stem = stem.split("(")[0].split("[")[0]
    stem = stem.strip()

    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[1].strip(), parts[0].strip()
    else:
        stem = re.sub(r"^\d+[\s._-]*", "", stem)
        return stem.strip(), None


def check_existing_lyrics(file_path):
    if not MUTAGEN_AVAILABLE:
        return False

    try:
        audio = MP3(file_path, ID3=ID3)

        if not audio.tags:
            return False

        return any(k.startswith("USLT") for k in audio.tags.keys())
    except Exception:
        return False


def fetch_lyrics(title, artist, duration=None):
    if not SYNCEDLYRICS_AVAILABLE or not title:
        return None

    try:
        search_query = f"{title} {artist}" if artist else title

        lyrics = syncedlyrics.search(
            search_query,
            synced_only=True,
            providers=["Lrclib", "Musixmatch", "Deezer", "NetEase"],
        )

        return lyrics
    except Exception as e:
        print(f"      Lyrics fetch error: {e}")
        return None


def embed_lyrics(file_path, lyrics_text):
    if not MUTAGEN_AVAILABLE or not lyrics_text:
        return False

    try:
        audio = MP3(file_path, ID3=ID3)

        if audio.tags is None:
            audio.add_tags()

        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_text))

        audio.save()
        return True
    except Exception as e:
        print(f"      Lyrics embed error: {e}")
        return False


def process_lyrics_for_file(file_path, filename):
    if not MUTAGEN_AVAILABLE or not SYNCEDLYRICS_AVAILABLE:
        return "skipped_no_libs"

    if check_existing_lyrics(file_path):
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
    lyrics = fetch_lyrics(title, artist, duration)

    if not lyrics:
        print("      No lyrics found")
        return "failed_not_found"

    if embed_lyrics(file_path, lyrics):
        print("      Lyrics embedded successfully")
        return "success"
    else:
        return "failed_embed"


def convert_audio_to_mp3():
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

    converted_count = 0
    skipped_count = 0
    error_count = 0

    lyrics_stats = {
        "success": 0,
        "failed_not_found": 0,
        "failed_no_title": 0,
        "failed_embed": 0,
        "skipped_exists": 0,
        "skipped_no_libs": 0,
    }

    print("Audio to MP3 Converter Started")
    print(f"Source: {current_dir}")
    print(f"Output: {compressed_dir}")

    if MUTAGEN_AVAILABLE and SYNCEDLYRICS_AVAILABLE:
        print("Lyrics embedding: ENABLED")
    else:
        print("Lyrics embedding: DISABLED (missing libraries)")

    print("-" * 50)

    for root, _, files in os.walk(current_dir):
        root_path = Path(root)

        if compressed_dir in root_path.parents or root_path == compressed_dir:
            continue

        audio_files = [f for f in files if Path(f).suffix.lower() in audio_extensions]
        mp3_files = [f for f in files if Path(f).suffix.lower() == ".mp3"]

        if audio_files or mp3_files:
            rel_path = root_path.relative_to(current_dir)
            output_dir = compressed_dir / rel_path
            output_dir.mkdir(parents=True, exist_ok=True)

            print(f"\nProcessing: {rel_path}")

            for filename in audio_files:
                input_file = root_path / filename
                output_file = output_dir / f"{Path(filename).stem}.mp3"

                if output_file.exists():
                    print(f"   Skip: {filename} (already exists)")
                    skipped_count += 1
                    continue

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
                        "-y",
                        str(output_file),
                    ]

                    subprocess.run(cmd, capture_output=True, text=True, check=True)

                    print(f"   Converted: {filename}")
                    converted_count += 1

                    time.sleep(0.5)
                    result = process_lyrics_for_file(output_file, filename)
                    if result in lyrics_stats:
                        lyrics_stats[result] += 1

                except subprocess.CalledProcessError as e:
                    print(f"   Error converting {filename}: {e}")
                    error_count += 1
                    continue
                except Exception as e:
                    print(f"   Unexpected error with {filename}: {e}")
                    error_count += 1
                    continue

            for filename in mp3_files:
                input_file = root_path / filename
                output_file = output_dir / filename

                if output_file.exists():
                    print(f"   Skip: {filename} (already exists)")
                    skipped_count += 1
                    continue

                try:
                    shutil.copy2(input_file, output_file)
                    print(f"   Copied: {filename}")
                    converted_count += 1

                    time.sleep(0.5)
                    result = process_lyrics_for_file(output_file, filename)
                    if result in lyrics_stats:
                        lyrics_stats[result] += 1

                except Exception as e:
                    print(f"   Error copying {filename}: {e}")
                    error_count += 1

    print("\n" + "=" * 50)
    print("CONVERSION COMPLETE")
    print(f"Files processed: {converted_count}")
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
        convert_audio_to_mp3()
    else:
        print("Operation cancelled.")
