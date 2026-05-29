#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import json

# Add current folder to sys.path so we can import local modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from download_audio import download_audio
from analyze_audio import analyze_audio
from generate_html import generate_html

def run_pipeline(url, config_path, output_mp4, preview_mode, cmd_overrides, trim=True, trim_start=15.0, trim_duration=35.0):
    # Establish absolute paths
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(pipeline_dir, "assets")
    
    audio_path = os.path.join(assets_dir, "audio.mp3")
    analysis_path = os.path.join(assets_dir, "analysis.json")
    html_path = os.path.join(pipeline_dir, "index.html")
    meta_path = os.path.join(pipeline_dir, "meta.json")
    
    os.makedirs(assets_dir, exist_ok=True)
    
    print("="*60)
    print(" STARTING TIKTOK-HYPERPIPELINE ")
    print("="*60)
    
    # 1. Download Audio
    try:
        download_audio(url, audio_path, trim=trim, trim_start=trim_start, trim_duration=trim_duration)
    except Exception as e:
        print(f"Error during audio download: {e}")
        return False
        
    # 2. Analyze Audio
    try:
        success = analyze_audio(audio_path, analysis_path)
        if not success:
            return False
    except Exception as e:
        print(f"Error during audio analysis: {e}")
        return False
        
    # 3. Handle Overrides and Config
    try:
        config = {}
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
        
        # Apply command-line overrides if any
        if cmd_overrides.get("title"):
            config["title"] = cmd_overrides["title"]
        if cmd_overrides.get("intro_text"):
            config["intro_text"] = cmd_overrides["intro_text"]
        if cmd_overrides.get("drop_texts"):
            config["drop_texts"] = [t.strip() for t in cmd_overrides["drop_texts"].split(",")]
        if cmd_overrides.get("outro_text"):
            config["outro_text"] = cmd_overrides["outro_text"]
        if cmd_overrides.get("color_primary") or cmd_overrides.get("color_secondary"):
            if "theme" not in config:
                config["theme"] = {}
            if cmd_overrides.get("color_primary"):
                config["theme"]["color_primary"] = cmd_overrides["color_primary"]
            if cmd_overrides.get("color_secondary"):
                config["theme"]["color_secondary"] = cmd_overrides["color_secondary"]
                
        # Save merged config back
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
            
    except Exception as e:
        print(f"Error merging config and overrides: {e}")
        return False
        
    # 4. Generate HTML Composition
    try:
        success = generate_html(analysis_path, config_path, html_path)
        if not success:
            return False
    except Exception as e:
        print(f"Error generating HTML: {e}")
        return False
        
    # Create a basic meta.json required by HyperFrames if it doesn't exist
    if not os.path.exists(meta_path):
        meta_data = {
            "name": "tiktok_video",
            "id": "tiktok_video"
        }
        with open(meta_path, "w") as f:
            json.dump(meta_data, f, indent=4)
            
    # 5. Run HyperFrames CLI
    print("="*60)
    if preview_mode:
        print(" RUNNING PREVIEW MODE (HOT RELOAD) ")
        print(" Open the link printed below in your browser.")
        print(" Press Ctrl+C in terminal to stop preview.")
        print("="*60)
        try:
            subprocess.run(["npx", "hyperframes", "preview"], cwd=pipeline_dir)
        except KeyboardInterrupt:
            print("\nPreview stopped.")
    else:
        print(" RENDERING FINAL VIDEO ")
        print(f" Output target: {output_mp4}")
        print("="*60)
        
        # Absolute path for output MP4 to make sure it saves correctly
        abs_output_mp4 = os.path.abspath(output_mp4)
        
        cmd = [
            "npx", "hyperframes", "render",
            "--output", abs_output_mp4
        ]
        
        try:
            res = subprocess.run(cmd, cwd=pipeline_dir)
            if res.returncode == 0:
                print("="*60)
                print(" SUCCESS! VIDEO RENDERED SUCCESSFULLY ")
                print(f" Video file saved to: {abs_output_mp4}")
                print("="*60)
                return True
            else:
                print(f"Error: HyperFrames render command returned exit code {res.returncode}")
                return False
        except Exception as e:
            print(f"Error executing hyperframes render: {e}")
            return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Automated TikTok Video Generator with Beat Reactivity")
    parser.add_argument('--url', required=True, help="YouTube/TikTok/Spotify audio link to download")
    parser.add_argument('--config', default="config.json", help="Path to config.json containing texts and styles")
    parser.add_argument('--output', default="output.mp4", help="Filename of the rendered MP4 output video")
    parser.add_argument('--preview', action='store_true', help="Start the HyperFrames interactive browser preview instead of rendering")
    parser.add_argument('--no-trim', action='store_true', help="Disable automatic audio trimming")
    parser.add_argument('--trim-start', type=float, default=15.0, help="Trim start time in seconds")
    parser.add_argument('--trim-duration', type=float, default=35.0, help="Trim duration in seconds")
    
    # Text and design overrides
    parser.add_argument('--title', help="Override title branding text")
    parser.add_argument('--intro', help="Override intro text hook")
    parser.add_argument('--drop-texts', help="Override drop climax texts (comma-separated list)")
    parser.add_argument('--outro', help="Override outro call to action text")
    parser.add_argument('--color-primary', help="Override primary accent color (e.g. #ff00ff)")
    parser.add_argument('--color-secondary', help="Override secondary accent color (e.g. #00ffff)")
    
    args = parser.parse_args()
    
    # Group overrides
    overrides = {
        "title": args.title,
        "intro_text": args.intro,
        "drop_texts": args.drop_texts,
        "outro_text": args.outro,
        "color_primary": args.color_primary,
        "color_secondary": args.color_secondary
    }
    
    # Path of config file relative to hyperpipeline directory
    config_filename = os.path.basename(args.config)
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(pipeline_dir, config_filename)
    
    run_pipeline(args.url, config_path, args.output, args.preview, overrides, 
                 trim=not args.no_trim, trim_start=args.trim_start, trim_duration=args.trim_duration)
