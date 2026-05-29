#!/usr/bin/env python3
import os
import argparse
import subprocess
import yt_dlp

def download_audio(url, output_path, trim=True, trim_start=15.0, trim_duration=35.0):
    print(f"Downloading audio from {url}...")
    
    # Ensure parent directory of output_path exists
    parent_dir = os.path.dirname(output_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
        
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path.replace('.mp3', ''),  # Temporary output template
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    # Ensure it's named with .mp3
    temp_mp3 = output_path.replace('.mp3', '') + '.mp3'
    if os.path.exists(temp_mp3) and temp_mp3 != output_path:
        os.rename(temp_mp3, output_path)
        
    # Trim the audio if required (focus on the buildup and drop, e.g. 15s to 50s)
    if trim:
        print(f"Trimming audio to segment: start={trim_start}s, duration={trim_duration}s...")
        trimmed_temp = output_path.replace('.mp3', '_temp_trimmed.mp3')
        
        # Use FFmpeg to trim and re-encode for accuracy
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(trim_start),
            "-t", str(trim_duration),
            "-i", output_path,
            "-acodec", "libmp3lame",
            trimmed_temp
        ]
        
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                os.replace(trimmed_temp, output_path)
                print("Audio successfully trimmed.")
            else:
                print(f"FFmpeg trim warning: exit code {res.returncode}. Keeping full audio.")
                if os.path.exists(trimmed_temp):
                    os.remove(trimmed_temp)
        except Exception as e:
            print(f"FFmpeg trim error: {e}. Keeping full audio.")
            if os.path.exists(trimmed_temp):
                os.remove(trimmed_temp)
                
    print(f"Audio downloaded and saved to: {output_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Download audio from a YouTube/TikTok/Insta URL")
    parser.add_argument('--url', required=True, help="URL of the video to extract audio from")
    parser.add_argument('--output', default="assets/audio.mp3", help="Output path for the MP3 file")
    parser.add_argument('--no-trim', action='store_true', help="Disable automatic trimming")
    parser.add_argument('--trim-start', type=float, default=15.0, help="Start time in seconds for trimming")
    parser.add_argument('--trim-duration', type=float, default=35.0, help="Duration in seconds for trimming")
    args = parser.parse_args()
    
    download_audio(args.url, args.output, trim=not args.no_trim, trim_start=args.trim_start, trim_duration=args.trim_duration)
