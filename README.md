# 🎬 YouTube Highlight Extractor

Automatically detects the **most engaging segment** of any YouTube video and extracts it as a standalone ~60-second highlight clip.

## How It Works

The tool uses a **cascading engagement detection pipeline** with 4 strategies (in priority order):

| # | Strategy | Accuracy | Data Source |
|---|----------|----------|-------------|
| 1 | **YouTube "Most Replayed" Heatmap** | ⭐⭐⭐⭐⭐ | YouTube's built-in replay data |
| 2 | **Transcript Density Analysis** | ⭐⭐⭐ | Speech rate, keywords, punctuation |
| 3 | **Chapter-Based Heuristic** | ⭐⭐ | Chapter titles + position scoring |
| 4 | **Position Fallback (65% mark)** | ⭐ | Statistical research on video peaks |

Each strategy is attempted in order. The first one that returns valid data is used.

## Prerequisites

- **Python 3.10+**
- **yt-dlp** — for video metadata and downloading
- **ffmpeg** — for video clipping (must be in your system PATH)

## Installation

```bash
# 1. Clone or download this project
cd Youtube

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Ensure yt-dlp is installed
pip install yt-dlp

# 4. Ensure ffmpeg is installed and in PATH
# Windows: Download from https://ffmpeg.org/download.html and add to PATH
# macOS:   brew install ffmpeg
# Linux:   sudo apt install ffmpeg
```

## Usage

### Single Video
```bash
python highlight_extractor.py https://www.youtube.com/watch?v=VIDEO_ID
```

### Custom Clip Duration (30-120 seconds)
```bash
python highlight_extractor.py URL --duration 90
```

### Multiple Videos
```bash
python highlight_extractor.py URL1 URL2 URL3
```

### Batch Processing (from file)
```bash
# Create a file with one URL per line
python highlight_extractor.py --batch urls.txt
```

### Custom Output Directory
```bash
python highlight_extractor.py URL --output my_highlights/
```

### Verbose Mode (debug logging)
```bash
python highlight_extractor.py URL --verbose
```

### Full Example
```bash
python highlight_extractor.py \
  https://www.youtube.com/watch?v=dQw4w9WgXcQ \
  --duration 60 \
  --output highlights/ \
  --verbose
```

## CLI Reference

```
usage: highlight_extractor [-h] [--batch FILE] [--duration SECONDS]
                           [--output DIR] [--verbose]
                           [urls ...]

positional arguments:
  urls                    One or more YouTube video URLs

options:
  -h, --help              Show help message
  --batch, -b FILE        Text file with URLs (one per line)
  --duration, -d SECONDS  Clip duration (default: 60, range: 30-120)
  --output, -o DIR        Output directory (default: ./output)
  --verbose, -v           Enable debug logging
```

## Output

- Clips are saved to `./output/` (or your specified directory) as MP4 files
- Filename format: `{Video_Title}_highlight.mp4`
- Quality: 1080p max, CRF 18 (visually lossless), AAC 192kbps audio

## Project Structure

```
Youtube/
├── highlight_extractor.py      # Main CLI entry point
├── requirements.txt            # Python dependencies
├── README.md                   # This file
├── modules/
│   ├── __init__.py
│   ├── analyzer.py             # Engagement analysis (4 strategies)
│   ├── downloader.py           # Video downloading & clipping
│   └── utils.py                # URL validation, formatting, logging
└── output/                     # Generated clips (created automatically)
```

## Engagement Detection Details

### Strategy 1: YouTube Heatmap (Most Replayed)
YouTube provides "Most Replayed" data for many popular videos. This appears as a heatmap overlay on the video progress bar. The tool extracts this data via yt-dlp and uses a sliding window algorithm to find the highest-engagement 60-second window.

### Strategy 2: Transcript Density
When heatmap data isn't available, the tool analyzes the video's transcript. It computes a composite score based on:
- **Speech density** (words per second)
- **Engagement keywords** ("amazing", "watch this", "key moment", etc.)
- **Punctuation energy** (questions and exclamations)

### Strategy 3: Chapter Analysis
For videos with chapters, the tool scores each chapter based on:
- **Positional scoring** (chapters at 50-80% of the video score highest)
- **Title keyword matching** ("best", "highlight", "reveal", etc.)
- **Intro/outro penalty** (first and last chapters are deprioritized)

### Strategy 4: Position Fallback
When no other data is available, the tool selects a segment at the **65% mark** of the video. Research suggests engagement peaks tend to occur between 55-75% of total duration.

## Error Handling

The tool handles these edge cases:
- ❌ Invalid YouTube URLs
- ❌ Private or deleted videos
- ❌ Region-locked content
- ❌ Missing yt-dlp or ffmpeg
- ⚠️ Very short videos (< 30s)
- ⚠️ Network timeouts
- 🧹 Automatic cleanup of temporary files on failure

## License

MIT
