# YT Downloader

A simple desktop app for downloading videos and audio, built with Python, PyQt5 and
[yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Features

- Quality choices: Best, 1080p, 720p, 480p and Audio Only
- Pause, resume and cancel
- Progress, file size and time remaining
- Embeds English subtitles in videos when available
- No account, login or browser cookies needed

## Download and use (Windows 10/11, 64-bit)

You don't need to install Python, FFmpeg or Deno.

1. Open the [latest release](../../releases/latest).
2. Under **Assets**, download `YTDownloader.exe`.
3. Double-click it. It may take a few seconds to open.
4. Paste a video link.
5. Choose a quality.
6. Click **Download** or press Enter.
7. Find the file in your **Downloads** folder.

### "Windows protected your PC"

Windows may show this because the app is not code-signed. If you downloaded it from this
repository's Releases page and trust it, click **More info**, then **Run anyway**.
Some antivirus programs may also flag it.

## Good to know

- Only public videos are supported. Private, members-only and age-restricted videos
  won't download.
- A playlist link may download every video in the playlist.
- A `~` before a size means it is an estimate.
- **Audio Only** keeps the audio format provided by the site. It does not convert to MP3.
- If a file with the same name already exists, it may not be downloaded again. Rename or
  move the old file first.
- While downloading, a temporary `.ytdl-tmp-...` folder appears in Downloads. It is removed
  when the download finishes, is cancelled or the app is closed.
- If a download fails, that folder is kept so a retry with the same link and quality can
  try to resume.

## Troubleshooting

| Problem | What to try |
|---|---|
| "Too many requests" or a bot check | Wait a while, then retry. |
| Downloads suddenly stop working | Get the newest release. From source, update yt-dlp (see below). |
| Network error | Check your internet connection and retry. |
| Quality not available | Choose **Best** or another quality. |
| Permission or disk space error | Close programs using the file and free up space. |
| A temporary folder was left behind | Close the app, then delete the `.ytdl-tmp-...` folder. |

## Run from source (developers)

### Requirements

- Python 3.10 or newer
- [Git](https://git-scm.com/) (the commands below use Git Bash on Windows)
- FFmpeg and Deno

### Steps

1. Install FFmpeg and Deno, then restart your terminal:

   ```bash
   winget install -e --id Gyan.FFmpeg.Essentials
   winget install -e --id DenoLand.Deno
   ```

2. Get the code:

   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
   cd YOUR_REPO
   ```

3. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/Scripts/activate
   ```

   In PowerShell, activate it with `.\.venv\Scripts\Activate.ps1`.

4. Install the packages and run the app:

   ```bash
   python -m pip install -r requirements.txt
   python app.py
   ```

To update yt-dlp:

```bash
python -m pip install -U "yt-dlp[default]"
```

## Build the .exe

Complete the steps above first, then run these in Git Bash.

1. Install the build tools:

   ```bash
   python -m pip install pyinstaller pillow
   ```

2. Copy FFmpeg and Deno into the project folder:

   ```bash
   cp "$(command -v ffmpeg)" ffmpeg.exe
   cp "$(command -v ffprobe)" ffprobe.exe
   cp "$(command -v deno)" deno.exe
   ```

3. Build:

   ```bash
   python -m PyInstaller --noconfirm --clean --onefile --windowed --name YTDownloader --icon icon1.png --add-data "icon1.png;." --add-binary "ffmpeg.exe;." --add-binary "ffprobe.exe;." --add-binary "deno.exe;." --collect-all yt_dlp --collect-all yt_dlp_ejs app.py
   ```

The app is created at `dist/YTDownloader.exe`.

## Disclaimer

This project is for personal and educational use. Only download content that you own or
have permission to download, and follow copyright law and each site's terms of service.
You are responsible for how you use this app. It is provided "as is", without warranty.

This project is not affiliated with or endorsed by YouTube or Google.

## AI assistance

AI tools, **Claude** (Anthropic) and **Gemini** (Google), were used to help where I got
stuck and to improve the overall quality of the code.

## Third-party software

| Software | License |
|---|---|
| [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) | GPL v3 |
| [FFmpeg](https://ffmpeg.org/) ([gyan.dev build](https://www.gyan.dev/ffmpeg/builds/)) | GPL v3 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense |
| [Deno](https://github.com/denoland/deno) | MIT |

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**.
See the `LICENSE` file for details.
