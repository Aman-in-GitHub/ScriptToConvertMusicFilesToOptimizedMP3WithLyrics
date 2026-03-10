# Optimized MP3 With Lyrics

Converts all audio files in the current folder (and subfolders) to optimized MP3s. Existing MP3s are copied as-is. Synced lyrics can be embedded into MP3 tags (requires optional libraries).

## Requirements
- Python 3
- `ffmpeg` in your PATH
- Optional (lyrics): `mutagen`, `syncedlyrics`

## Install dependencies
```bash
uv sync
```

## Run
```bash
uv run main.py
```

## Run with cli flags
```bash
uv run main.py --workers 4 --lyrics-workers 4
```

## Workers
- `--workers`: Number of parallel audio conversion tasks. Higher values speed up processing on multi-core CPUs but can increase CPU load.
- `--lyrics-workers`: Number of parallel lyric lookup/embed tasks. Useful when fetching synced lyrics; keep this lower if you want to avoid rate limits.
